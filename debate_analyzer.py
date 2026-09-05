"""
Debate Analyzer v2.1 — Multi-pass argumentation analysis.

Supports both OpenAI and Anthropic as analysis providers:
  config.yaml → analysis.provider: "anthropic" | "openai"

Improvements over v1:
  • Dual provider support (Claude / GPT switchable via config)
  • Multi-pass analysis (claims → structure → rebuttals → fallacies → synthesis)
  • Rhetoric & emotion analysis pass
  • i18n support (EN/SL) via translations module
"""

import hashlib
import json
import logging
import os
import re
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from dotenv import load_dotenv

from config_loader import get as cfg, model_supports_temperature, sampling_kwargs
from cache import get_cache
from translations import t, label, get_verdict_label

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent



def _compact_json(obj: Any) -> str:
    """Serialize an object as compact JSON for embedding in LLM prompts.

    No indent padding and no \\uXXXX escaping (ensure_ascii=False keeps
    Slovenian/Unicode chars as-is). Both reduce input tokens vs json.dumps(
    obj, indent=1) with zero loss of information — the model reads the same
    data, we just pay for fewer characters on every pass that forwards a
    previous pass's output.
    """
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _fit_compact_json(obj: Any, limit: int) -> str:
    """Skrči objekt na dano dolžino, ne da bi razrezal JSON.

    Odstranjuje zadnje elemente najdaljšega seznama, da se krajšanje porazdeli
    med govorce, namesto da bi zadnjega odrezalo v celoti.
    """
    text = _compact_json(obj)
    if len(text) <= limit or not isinstance(obj, (dict, list)):
        return text[:limit]

    import copy
    trimmed = copy.deepcopy(obj)

    def _longest_list(node: Any, best: Optional[list] = None) -> Optional[list]:
        if isinstance(node, list):
            if len(node) > (len(best) if best is not None else 0):
                best = node
            children = node
        elif isinstance(node, dict):
            children = node.values()
        else:
            return best
        for v in children:
            best = _longest_list(v, best)
        return best

    while len(text) > limit:
        lst = _longest_list(trimmed)
        if not lst:  # nothing left to trim
            break
        lst.pop()
        text = _compact_json(trimmed)

    return text[:limit]


def _strip_markdown_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _extract_first_json_object(text: str) -> str:
    """Return the first complete top-level JSON object from text."""
    start = text.find("{")
    if start == -1:
        return text

    depth = 0
    in_string = False
    escaped = False

    for i in range(start, len(text)):
        ch = text[i]

        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]

    return text[start:]


class EmptyModelResponseError(json.JSONDecodeError):
    """The provider returned a message with no text content at all.

    Seen on very large inputs; a retry with the same (smaller) input usually
    succeeds, so it is treated like any other parse failure and retried."""


class TruncatedJSONError(json.JSONDecodeError):
    """Raised when the model output looks cut off rather than merely malformed."""


def _looks_truncated_json(text: str, exc: json.JSONDecodeError, stop_reason: Optional[str] = None) -> bool:
    stripped = text.rstrip()
    near_end = exc.pos >= max(len(stripped) - 80, 0)

    if stop_reason in {"max_tokens", "length"}:
        return True
    # Any "Unterminated string" in LLM-produced JSON is almost always truncation:
    # a well-formed model never legitimately leaves a string without its closing
    # quote. Even if the cut happens deep in the document (not near the end of
    # the buffer), the output is still cut off — the rest never arrived.
    if "Unterminated string" in exc.msg:
        return True
    # Same logic for "Expecting" mid-document errors that happen far from end —
    # if the doc is heavily unbalanced, it was almost certainly truncated.
    if stripped.count("{") > stripped.count("}") + 1:
        return True
    if "[" in stripped and stripped.count("[") > stripped.count("]") + 1:
        return True
    if stripped.count("{") > stripped.count("}") and near_end:
        return True
    if stripped.endswith((",", ":", "\\", '"')):
        return True
    return False


def _loads_llm_json(raw: str, stop_reason: Optional[str] = None) -> Dict:
    """Best-effort parser for model JSON output."""
    cleaned = _strip_markdown_fences(raw)
    extracted = _extract_first_json_object(cleaned)

    last_exc: Optional[json.JSONDecodeError] = None
    for candidate in [cleaned, extracted]:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
            return parsed if isinstance(parsed, dict) else {"result": parsed}
        except json.JSONDecodeError as exc:
            last_exc = exc

    if last_exc and _looks_truncated_json(extracted or cleaned, last_exc, stop_reason):
        raise TruncatedJSONError(last_exc.msg, last_exc.doc, last_exc.pos)
    if last_exc:
        raise last_exc
    # An empty answer that stopped at the token limit is a truncation: the budget
    # ran out before any text was emitted (newer models spend part of it on
    # internal reasoning). Raise the truncation error so the retry raises the
    # budget instead of repeating the same doomed call three times.
    if stop_reason in {"max_tokens", "length"}:
        raise TruncatedJSONError(
            f"Empty model response — budget exhausted (stop_reason={stop_reason})", raw, 0)
    raise EmptyModelResponseError(
        f"Empty model response (stop_reason={stop_reason})", raw, 0)


# ── PROMPTS ─────────────────────────────────────────────────────────────────

# Edino, kar izluščanje potrebuje od hišnih pravil. Ta korak ne presoja, ampak
# prepiše: iz prepisa naredi seznam argumentov. Nevtralnost je tu vprašanje
# popolnosti in ne ocene — argument, ki bi bil izpuščen ali prepisan v šibkejši
# obliki, poznejšim korakom sploh ne pride v roke in ga noben ne more popraviti.
def _recording_rules(mode: str = "debate_1v1") -> str:
    """Pravila o tem, kdo v posnetku šteje za udeleženca.

    Dobi jih samo korak, ki bere prepis. Kdo je moderator in kdo naključni
    glas, se odloči enkrat, ob izluščanju argumentov. Poznejši koraki dobijo
    izluščene argumente, v katerih teh glasov ni več, zato jim pravilo o njih
    ne pove ničesar o nalogi, ki jo opravljajo.
    """
    is_single_speaker = mode in ("solo", "reaction")
    single_speaker_rule = (
        "SINGLE-SPEAKER MODE (CRITICAL): This analysis covers ONE primary speaker who is "
        "either (a) presenting their own arguments, (b) responding to external criticism / "
        "content (a video being reacted to, an article, a tweet, a prior statement), or "
        "(c) a mix of both. The unifying frame: a single person is reasoning out loud.\n"
        "\n"
        "PRIMARY SPEAKER:\n"
        "  • Identify the one person whose argumentation we judge — usually the host/uploader/"
        "creator/reactor/interviewee. They are the person DELIVERING analysis or opinion.\n"
        "  • Score arguments, fallacies, rhetoric ONLY for the primary speaker. Other voices "
        "are CONTEXT, not content.\n"
        "  • Add ONLY the primary speaker to `speakers`. Do NOT add interviewers, hosts, "
        "audience members, original-content speakers, or any other voice as a separate speaker.\n"
        "\n"
        "RESPONDING TO EXTERNAL CONTENT: If the speaker is reacting to a video, article "
        "or post, use it as CONTEXT only — do NOT extract the original creator's arguments "
        "as if they were a participant in this recording. The same applies to an "
        "interviewer's questions: fold the question's substance into the speaker's answer "
        "so the extracted argument stands on its own.\n"
        "\n"
        "PRESENTATION OF OTHERS' ARGUMENTS (philosophers, thinkers, prior figures):\n"
        "  • A speaker may PRESENT or EXPLAIN arguments from someone else (Aquinas's Five Ways, "
        "Kant's categorical imperative, Marx's theory of surplus value, etc.). Treat presented "
        "arguments AS IF the speaker is arguing them — they chose to present, so they own the "
        "presentation. Attribute to the primary speaker, NOT the historical figure.\n"
        "  • Evaluate the QUALITY of the presentation: representation accuracy, premise quality, "
        "explanatory clarity. Speaker's own commentary on top → separate argument.\n"
        "\n"
        "VOICES THAT ARE NOT THE PRIMARY SPEAKER (interviewer, host, audience, original-content "
        "speaker, off-camera crew, brief interjections): treat exactly like a moderator. CONTEXT, "
        "not content. Use to interpret responses; do NOT score, do NOT extract as own arguments, "
        "do NOT flag their words as fallacies. If irrelevant chatter (heckles, technical asides) "
        "that the primary speaker doesn't engage with, IGNORE entirely.\n"
        if is_single_speaker else ""
    )

    # ── Debate-mode rule: strictly 1v1 (two debaters, moderator excluded) ──
    is_debate = mode == "debate" or mode == "debate_1v1"
    debate_rule = (
        "DEBATE MODE — EXACTLY TWO DEBATERS (1v1, CRITICAL):\n"
        "This system analyses ONLY one-on-one debates: exactly TWO debaters holding "
        "opposing positions. A moderator, host, interviewer or audience member is NOT a "
        "debater and does not count toward the two (see the moderator rule below).\n"
        "\n"
        "RULES:\n"
        "  • `speakers` must contain EXACTLY the two debaters — never more, never fewer.\n"
        "  • A purely defensive participant (only rebuts, builds no own case) IS one of the "
        "two debaters — give them an entry with an empty `arguments` list.\n"
        "  • Map rebuttals only between these two (A→B and B→A).\n"
        "  • If the recording genuinely has THREE OR MORE people actively defending distinct "
        "positions, do NOT pick two arbitrarily and do NOT merge them. Instead return the two "
        "most active as `speakers` AND set `metadata.too_many_debaters` to true, listing every "
        "detected debater in `metadata.detected_debaters`. The pipeline stops the analysis and "
        "tells the user — a wrong guess is worse than a clear refusal.\n"
        "  • If only ONE person argues (no opponent), set `metadata.too_few_debaters` to true — "
        "that recording belongs in solo mode.\n"
        if is_debate else ""
    )

    return (
        single_speaker_rule
        + debate_rule
        +         "WHO COUNTS AS A PARTICIPANT:\n"
        "0. MODERATOR RULE (only applies if a moderator is present): Some debates include a moderator "
        "whose role is to ask questions, introduce topics, and facilitate — NOT to argue a position. "
        "A moderator is recognizable because they almost exclusively ask questions, summarize, or hand off — "
        "and never defend a stance of their own. "
        "MANY DEBATES HAVE NO MODERATOR — do not force anyone into this role if everyone is actively debating. "
        "\n"
        "  IF a moderator IS present, treat them as CONTEXT, NOT CONTENT:\n"
        "    • DO use their questions, sub-questions, and summaries to UNDERSTAND what each debater "
        "is responding to. A debater's short answer (\"yes\", \"obviously\", \"that's exactly my point\") "
        "is only meaningful given the question that preceded it — fold that question into the debater's "
        "extracted argument so it stands on its own.\n"
        "    • DO use moderator summaries (\"so you're saying X\") as a BRIDGE: if debater B then responds, "
        "they are engaging with debater A's argument (channeled through the moderator), not the moderator.\n"
        "    • DO NOT add the moderator to `speakers`. DO NOT extract their own arguments. DO NOT score "
        "their rhetoric. DO NOT flag their words as fallacies. DO NOT include them in the "
        "per-speaker evaluation. A moderator is not a debater.\n"
        "    • Moderator questions are FACILITATION, not rebuttals — never list them as rebuttals or "
        "as evasion targets between debaters.\n"
        "    • DO record the moderator separately in `metadata.moderator` (see the output schema): "
        "their name, how many questions they asked, the questions themselves, and whether they "
        "pushed one side harder than the other. This is REPORTING, not scoring — the reader "
        "should be able to see how much the moderator shaped the exchange.\n"
        "  If there is NO moderator, set `metadata.moderator.present` to false and ignore the rest.\n"
        "0b. INCIDENTAL VOICES (audience, off-camera crew, brief unnamed interjections): "
        "Same principle. If a random voice says something IRRELEVANT to the debate (heckles, technical chatter, "
        "asides), IGNORE it completely — do not extract it, do not flag it, do not add the speaker. "
        "If a non-debater voice raises a SUBSTANTIVE point that the actual debaters then engage with, "
        "treat that voice exactly like a moderator: context only, no own arguments, no own scoring — "
        "but use what they said to interpret the debaters' responses.\n"
    )


# ── SISTEMSKI POZIVI: EDEN NA KLIC ──────────────────────────────────────────
# Vsak klic ima svoj poziv, zapisan v celoti. Odstavek o nevtralnosti se zato
# ponovi trikrat, razhajanje kopij pa lovi test.

def _system_extraction(mode: str = "debate_1v1") -> str:
    """Korak 1: iz prepisa naredi seznam argumentov.

    Ta korak ne presoja, ampak prepiše, zato ne dobi meril za presojo. Dobi pa
    pravila o tem, kdo v posnetku šteje za udeleženca, saj edini odloča, koga
    vpiše med govorce. Nevtralnost je tu vprašanje popolnosti: argument, ki tu
    izpade, poznejšim korakom sploh ne pride v roke.
    """
    return (
        "You are extracting arguments from a recording, not judging them.\n"
        "\n"
        "Extract what each speaker ACTUALLY argued, whatever the topic. Do not omit an "
        "argument because you disagree with its conclusion, do not restate it in a weaker "
        "form than the speaker gave it, and do not add reasoning the speaker did not offer. "
        "Political, religious and ideological positions are extracted exactly like any other.\n"
        "\n"
        "Return ONLY valid JSON — no markdown, no commentary outside JSON.\n"
        "\n"
        + _recording_rules(mode)
        + t("llm.language_instruction")
    )


def _system_fallacies() -> str:
    """Korak 2: poimenuj zmote v sklepanju."""
    return (
        "You are an expert debate analyst with deep knowledge of argumentation theory, "
        "logic, rhetoric, and REAL-WORLD debate dynamics.\n"
        "Be rigorous, neutral, evidence-based, structured, and precise. Here 'neutral' "
        "means UNBIASED and evidence-driven: report weaknesses in the reasoning exactly "
        "where you find them, without softening them to keep the sides looking balanced "
        "and without declaring an overall winner.\n"
        "Analyze the REASONING, not personal opinions.\n"
        "ASSESS ONLY WHAT WAS SAID: Assess each side strictly on the merits of what they "
        "actually argued in THIS recording — the logic, the evidence they presented, and how "
        "they handled objections. Do NOT let your own views on the TOPIC (political, religious, "
        "ideological, moral) influence the assessment. You are assessing the QUALITY OF THE "
        "REASONING, not the truth of the position: a factually weaker side can still argue more "
        "rigorously, and you must report it that way.\n"
        "\n"
        "HOW STRICTLY TO JUDGE:\n"
        "1. CONSERVATIVE FALLACY DETECTION: Not every sharp remark or mild insult is an ad "
        "hominem fallacy. In real debates, speakers use colorful language, sarcasm, and pointed "
        "remarks — these are rhetorical tools, not fallacies, UNLESS the speaker uses them AS A "
        "SUBSTITUTE for addressing the argument. A true ad hominem attacks the PERSON instead of "
        "the ARGUMENT. A speaker who says 'that's ridiculous' and then explains why is NOT "
        "committing a fallacy. Only flag fallacies you are highly confident about.\n"
        "2. DEBATABLE vs FACTUAL: Not every claim needs a TRUE/FALSE verdict. Many positions in "
        "debates are legitimately debatable — matters of interpretation, values, policy "
        "preference, or contested evidence. Recognize when something is genuinely OPEN TO DEBATE "
        "rather than forcing a binary verdict.\n"
        "3. DEBATE DYNAMICS: Real debates involve pressure tactics, persistence, emotional "
        "moments and strategic behaviour. Analyse these as what they are — debate techniques — "
        "not as logical errors. A speaker who is passionate is not necessarily committing an "
        "appeal to emotion fallacy.\n"
        "\n"
        "Return ONLY valid JSON — no markdown, no commentary outside JSON."
        + t("llm.language_instruction")
    )


def _system_rebuttal() -> str:
    """Korak 4: preslikaj zavrnitve in izogibanja."""
    return (
        "You are an expert debate analyst with deep knowledge of argumentation theory, "
        "logic, rhetoric, and REAL-WORLD debate dynamics.\n"
        "Be rigorous, neutral, evidence-based, structured, and precise. Here 'neutral' "
        "means UNBIASED and evidence-driven: report weaknesses in the reasoning exactly "
        "where you find them, without softening them to keep the sides looking balanced "
        "and without declaring an overall winner.\n"
        "Analyze the REASONING, not personal opinions.\n"
        "ASSESS ONLY WHAT WAS SAID: Assess each side strictly on the merits of what they "
        "actually argued in THIS recording — the logic, the evidence they presented, and how "
        "they handled objections. Do NOT let your own views on the TOPIC (political, religious, "
        "ideological, moral) influence the assessment. You are assessing the QUALITY OF THE "
        "REASONING, not the truth of the position: a factually weaker side can still argue more "
        "rigorously, and you must report it that way.\n"
        "\n"
        "WHAT COUNTS AS EVASION:\n"
        "Pay close attention to when a speaker AVOIDS answering a direct question. If someone "
        "asks a question and the other person deflects, changes the subject, or gives a "
        "non-answer, this is a significant debate behaviour. When a speaker repeats the same "
        "question multiple times, it usually means the other side is REFUSING TO ANSWER — this "
        "is NOT a fallacy by the questioner, it is EVASION by the non-answerer.\n"
        "\n"
        "Return ONLY valid JSON — no markdown, no commentary outside JSON."
        + t("llm.language_instruction")
    )


def _system_synthesis() -> str:
    """Korak 5: povzemi, kar so prejšnji koraki ugotovili."""
    return (
        "You are an expert debate analyst with deep knowledge of argumentation theory, "
        "logic, rhetoric, and REAL-WORLD debate dynamics.\n"
        "Be rigorous, neutral, evidence-based, structured, and precise. Here 'neutral' "
        "means UNBIASED and evidence-driven: report weaknesses in the reasoning exactly "
        "where you find them, without softening them to keep the sides looking balanced "
        "and without declaring an overall winner.\n"
        "Analyze the REASONING, not personal opinions.\n"
        "ASSESS ONLY WHAT WAS SAID: Assess each side strictly on the merits of what they "
        "actually argued in THIS recording — the logic, the evidence they presented, and how "
        "they handled objections. Do NOT let your own views on the TOPIC (political, religious, "
        "ideological, moral) influence the assessment. You are assessing the QUALITY OF THE "
        "REASONING, not the truth of the position: a factually weaker side can still argue more "
        "rigorously, and you must report it that way.\n"
        "\n"
        "You are writing the summary the reader sees first. Report only what the earlier "
        "steps found; do not introduce arguments, fallacies or verdicts that are not in "
        "the material you were given.\n"
        "\n"
        "Return ONLY valid JSON — no markdown, no commentary outside JSON."
        + t("llm.language_instruction")
    )


# ── Video-title argument-count hint ──────────────────────────────────────────
# Listicle titles ("9 razlogov za X", "Top 10 Reasons...") announce the argument
# structure up front. When detected, the claim_extraction pass is instructed to
# mirror that enumeration instead of consolidating freely.

_NUMBER_WORDS = {
    # English
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "twenty": 20,
    # Slovenian
    "en": 1, "ena": 1, "eno": 1, "dva": 2, "dve": 2, "trije": 3, "tri": 3,
    "štirje": 4, "štiri": 4, "stiri": 4, "pet": 5, "šest": 6, "sest": 6,
    "sedem": 7, "osem": 8, "devet": 9, "deset": 10, "enajst": 11, "dvanajst": 12,
    "trinajst": 13, "štirinajst": 14, "petnajst": 15, "dvajset": 20,
}

# Nouns that typically follow the number in a listicle title (sl + en, stemmed).
_LIST_NOUN_RE = (
    r"(?:razlog\w*|argument\w*|način\w*|nacin\w*|dokaz\w*|točk\w*|tock\w*|"
    r"stvar\w*|mit\w*|napak\w*|primer\w*|lekcij\w*|dejst\w*|odgovor\w*|znak\w*|"
    r"resnic\w*|laž\w*|laz\w*|tez\w*|trditv\w*|"
    r"reason\w*|way\w*|point\w*|proof\w*|thing\w*|myth\w*|mistake\w*|"
    r"lesson\w*|fact\w*|example\w*|tip\w*|answer\w*|sign\w*|truth\w*|lie\w*|claim\w*)"
)


def _title_argument_count(title: str) -> int:
    """Detect an announced item count in a listicle-style video title.

    Matches e.g. "9 razlogov za vegetarijanstvo", "Devet razlogov...",
    "Top 10 Reasons Why...", "7 mitov o...". Returns 0 if no count found.
    """
    if not title:
        return 0
    t_low = title.lower()
    # Longest words first so "enajst" wins over "en", "štirinajst" over "štiri".
    words = sorted(_NUMBER_WORDS, key=len, reverse=True)
    num = r"(\d{1,2}|" + "|".join(re.escape(w) for w in words) + r")"
    m = re.search(r"\b" + num + r"\s+(?:naj\w+\s+)?" + _LIST_NOUN_RE, t_low)
    if not m:
        # "Top 10: ..." style without a list-noun
        m = re.search(r"\btop\s+(\d{1,2})\b", t_low)
        if not m:
            return 0
    raw = m.group(1)
    n = int(raw) if raw.isdigit() else _NUMBER_WORDS.get(raw, 0)
    return n if 2 <= n <= 30 else 0


def _title_hint_block(title: str) -> str:
    """Instruction block appended to the claim_extraction prompt when the
    video title is known. If the title announces N items, the extraction must
    mirror that enumeration."""
    title = (title or "").strip()
    if not title:
        return ""
    block = f'\n\nVIDEO TITLE: "{title}"\n'
    n = _title_argument_count(title)
    if n:
        block += (
            f"The title announces an enumerated list of {n} items — this is the "
            f"speaker's own enumeration, so the EXCEPTION rule applies and this is "
            f"the one case where a count is fixed in advance. The primary "
            f"speaker's `arguments` list MUST mirror that enumeration: exactly {n} "
            f"main arguments, one per announced item, in the order presented. "
            f"Locate each announced item in the transcript even when transitions "
            f"are subtle (speakers often don't say 'reason number four'). Fold the "
            f"intro, outro, and side comments into the relevant argument's "
            f"premises. Add an argument beyond the {n} ONLY if the speaker makes "
            f"a substantial, clearly independent point outside the enumeration."
        )
    else:
        block += (
            "Use the title as context: it tells you the topic and the speaker's "
            "likely framing. Do not invent arguments from the title alone — "
            "extract only what the transcript supports."
        )
    return block



PASS_PROMPTS = {
    "claim_extraction": """Extract EVERY argument each speaker actually makes in the transcript above.

The transcript may contain ONE speaker (solo) or MULTIPLE speakers (debate). If solo,
interview or reaction, include ONLY the primary speaker in `speakers` — for reactions
that is the reactor. If the speaker presents another thinker's argument ("Aquinas
argues that..."), attribute it to the speaker: they chose to present it.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WHAT COUNTS AS AN ARGUMENT — A TEST, NOT A JUDGEMENT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
An argument is one connected chain: premises → reasoning → conclusion. It ends when
the speaker moves to a different conclusion that does not rest on the same premises.

Do NOT decide which arguments are the "main" ones. That judgement is not reproducible:
two readings of one transcript pick different subsets, and the analysis then depends on
which subset was picked rather than on what was said. Apply this test instead, to every
conclusion the speaker asserts, in transcript order:

    Include it if — and only if — the speaker states a conclusion AND gives at least
    TWO reasons for it in this recording.

Nothing else decides inclusion: not how important the conclusion seems, not how many
entries you already have, not how much transcript is left. A bare assertion with no
reason is not an argument. Neither is a conclusion propped up by a single reason —
that lone reason is almost always a reason for something larger the speaker argues,
so attach it there as a premise instead of listing it alone.

NO TARGET COUNT: none is required, expected, or inferred from the recording's length.
If a speaker makes no complete argument, return an empty list for them — never invent
arguments to fill space. The only thing that may fix a count is the speaker's own
explicit enumeration (below).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GROUPING — BY POSITION, NOT BY JUDGEMENT OF SIZE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Do not weigh "fewer, richer" against "more, smaller"; that trade-off is what makes two
readings disagree. Group mechanically:

  STEP 1. List the distinct POSITIONS this speaker defends — the top-level claims they
          are here to establish. A position must be a claim that can be AFFIRMED OR
          DENIED as stated ("smartphones should be banned from classrooms"). It must
          NOT be a summary gesturing at a set of proposals ("society should take
          deliberate steps", "there are many harms") — such a wrapper swallows several
          real positions and hides them as premises. If your position cannot be argued
          against as written, it is a wrapper: replace it with the actual claims.
  STEP 2. Attach every conclusion that passed the test to the ONE position it supports.
          Each conclusion goes to exactly one position. If a claim would fit two, the
          positions overlap and must be merged or re-cut so nothing appears twice.
  STEP 3. Each position becomes ONE argument: the position is the `argument` text, the
          conclusions attached to it are its premises.

MERGE into one argument:
  • The same conclusion restated, hedged or elaborated — however far apart it sits.
    Speakers open with a thesis and close by restating it: that is ONE argument.
  • A series of cases, statistics or historical episodes that all answer the SAME
    question ("Germany after the Kaiser...", "Russia after the Tsar...") — one
    argument whose premises are those cases, not one argument per case.
  • A stepping stone and the conclusion it exists to license — the stepping stone
    becomes a premise. Likewise a whole CHAIN of principles that exists only to reach
    one final thesis: record each step as a premise of that single argument.

SEPARATE into distinct arguments:
  • A different conclusion that stands on its own, on premises of its own
  • A clear pivot: "another reason is...", "a second point is..."
  • An item explicitly announced as a separate entry in an enumerated list

EXCEPTION — EXPLICIT ENUMERATION FIXES THE COUNT: if the speaker or the video title
announces a numbered list ("nine reasons why...", "three arguments against..."), the
arguments MUST mirror it: one per announced item, in order. Do not merge two announced
reasons, do not split one.

COUNTER-EXCEPTION — ENUMERATED PREMISES ARE NOT ENUMERATED ARGUMENTS (CRITICAL):
Enumeration splits arguments only when each numbered item carries its own standalone
conclusion. A speaker enumerating the PREMISES of a single derivation ("premise one...
premise seven... THEREFORE God exists") is building ONE argument. Test each item:
"Does this item ALONE support the speaker's final position?"
    → works alone       → enumeration of ARGUMENTS, split per item
    → works only JOINTLY → ONE argument, the items are its premises
Signals of the one-argument case: the word "premise"/"premisa", a single
"therefore"/"torej" near the end, formal syllogistic structure, items that are
non-conclusive statements rather than reasons-for-a-position. A 15-minute video that
carefully builds ONE deductive argument is ONE argument with many premises — a correct
extraction, not a lazy one. If any draft entry describes ITSELF as a premise or a step
("this is the first premise"), you have made this mistake: merge those entries.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WHAT IS NOT AN ARGUMENT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  • A bare assertion the speaker never justifies — a position, not an argument
  • Asides, insults, mockery, interpersonal disputes — never arguments, and never
    premises. If an insult comes with a substantive reason, extract only the reason.
  • META-COMMENTARY about the argument itself: its persuasion record ("this has
    convinced thousands"), the speaker's history of using it, self-assessment of its
    quality ("it is undefeatable"). Test: is the conclusion about the debate's subject,
    or about the argument's reception? If the latter, leave it out — it still informs
    the fallacy pass, so nothing is lost.
  • SARCASM/IRONY: extract the speaker's ACTUAL position, not the surface words.
  • POSITION vs FACT: the position a speaker defends (moral, normative, policy) is the
    debate itself, not an error — describe it, judge nothing here.

DO NOT cap the output at an arbitrary number; do not inflate it with sub-points that
belong inside an argument; do not split one idea because it spans many lines; do not
collapse two distinct lines of reasoning because they sit next to each other; do not
write an argument longer than the speaker's point requires.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ARGUMENT TEXT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Target 1–3 sentences. Sentence 1 = the conclusion the speaker is claiming. Sentences
2–3 = the core reasoning, the "why" that links premises to conclusion. That is all.
Paraphrase to the essence — do not quote long stretches, do not pad with examples or
flourishes (those go in premises if they do real work, or are dropped). If the speaker
rambles, capture the spine, not the skin. Use more sentences only when the reasoning
genuinely needs them.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PREMISES — LOAD-BEARING MINI-ARGUMENTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
A premise is a statement the argument DEPENDS ON: remove it and the argument weakens
or falls. If removing it changes nothing, it is filler — leave it out.

Write each premise as a compact mini-argument when the speaker supports it:
"claim — because/since the reason they gave" (1–2 sentences). If they assert it without
support, record the bare claim — do NOT invent reasoning. Instead of "Meat production
emits CO2", write "Meat production drives emissions — the speaker cites FAO data
attributing ~15% of global greenhouse gases to livestock."

EACH PREMISE MUST: directly support the conclusion; be a complete self-standing claim;
add something the others do not already cover; pass "without this the argument would
not work".

NEVER as premises: restatements of the conclusion (circular); transitions and fillers;
purely illustrative examples; background facts that do not feed the conclusion; vague
gestures ("look at history") without specifics.

ONE PREMISE PER REASON — NOT PER PIECE OF EVIDENCE (CRITICAL):
The unit is a REASON, not a fact. When several figures, cases or studies support the
SAME reason, they belong in ONE premise: state the reason, then list the evidence
compactly inside that same string.

  WRONG — four premises, one reason:
    "Teen suicide rose 167% among girls and 91% among boys to 2020."
    "Eating-disorder admissions in the UK rose six-fold in a decade."
    "Self-harming among teens rose 500% in nine years."
    "One in three British children are now short-sighted."
  RIGHT — one premise:
    "Children's health indicators have worsened sharply — teen suicide up 167%
     (girls) and 91% (boys) to 2020, eating-disorder admissions six-fold in a decade,
     self-harm up 500% in nine years, one in three now short-sighted."

Keep the grouped premise telegraphic: figures preserved, narrative stripped. Lose no
number — compress the prose around it.

More than about eight premises on one argument signals that you listed EVIDENCE
separately instead of grouping it by reason: re-read and ask of each pair, "same reason,
different evidence?" If yes, merge. This is a check on the unit, not a quota — an
argument genuinely resting on many independent reasons keeps them all. If the speaker
EXPLICITLY enumerates their premises, record every announced premise that does real
work, in order; do not compress an explicit derivation to look tidier.

Extract premises faithfully, with zero editorializing. Philosophical, theological and
scientific axioms are valid starting points, not flaws — describe what was argued;
judgement happens in the next pass.

Argument types: factual | normative | causal | definitional | debatable
Use "debatable" for value judgements, policy preferences, contested interpretations.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WHO COUNTS AS A SPEAKER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Use speaker labels EXACTLY as they appear in the transcript. Never guess a real-world
name: a person mentioned, quoted or reacted to is a third party, not the speaker.

Add someone to `speakers` only if they actively defend a position. A moderator (asks
questions, summarizes, hands off) is not one; most debates have none — do not force
the role. A participant who only ATTACKS the opponent's case IS a debater: include them
with `position` filled and an EMPTY `arguments` list if they make no standalone
argument. Do not invent arguments to fill it — their rebuttals are captured later.

Record the moderator in `metadata.moderator` (present, label, question count, the
questions, whether they pressed one debater harder). This is description for the reader;
they never enter `speakers` and are never scored. USE their words to make the debaters'
arguments stand alone:
  • A short reply that only makes sense given the question before it ("It absolutely
    does.") → fold the question's substance into the argument so it is self-contained.
  • If the moderator summarizes debater A and B engages with that summary, B is
    responding to A — map it to A.
  • If the moderator narrows the topic, read the answer in that narrowed scope.

INCIDENTAL VOICES (audience, crew, one-off interjections): ignore chatter and heckles
the debaters do not engage with. If a non-debater raises a substantive point the
debaters address, treat it exactly like a moderator — context only, never in `speakers`.
A stray "Speaker (chunk 3)" with a line or two nobody engages with is almost always a
diarization artifact: skip it. If unsure whether someone is a participant, check whether
they take a stance AND the debaters answer them substantively; if neither, skip.

DEBATE MODE IS STRICTLY 1v1: `speakers` must hold EXACTLY the two debaters. If three or
more genuinely defend distinct positions, set `metadata.too_many_debaters` to true and
list them in `metadata.detected_debaters` — the pipeline will stop and tell the user
rather than silently analysing the wrong pair. If nobody opposes the main speaker, set
`metadata.too_few_debaters` to true.

Return JSON:
{
  "metadata": {
    "topic": "...",
    "participants": {"SPEAKER": "primary_speaker|debater|moderator"},
    "moderator": {
      "present": false,
      "name": "speaker label of the moderator, empty if none",
      "question_count": 0,
      "questions": ["each question or prompt the moderator put to a debater, verbatim or closely paraphrased"],
      "pressed_more": "name of the debater the moderator pressed harder, or 'balanced' / 'n/a'",
      "notes": "1 sentence — how the moderator shaped the exchange (framing, interruptions, topic changes). Empty if none."
    },
    "too_many_debaters": false,
    "too_few_debaters": false,
    "detected_debaters": ["fill ONLY when too_many_debaters is true — every person actively defending a position"]
  },
  "speakers": {
    "SPEAKER_NAME": {
      "position": "1-sentence summary of overall position",
      "arguments": [
        {
          "argument": "1-3 sentences: the conclusion + the core 'why'. Direct to the point, no padding. (FINAL/FULLEST version if the speaker developed it later.)",
          "type": "factual|normative|causal|definitional|debatable",
          "premises": ["mini-argument: load-bearing claim — plus the speaker's own reason for it, if given", "..."]
        }
      ],
      "conclusions": ["final conclusion 1"]
    }
  }
}
""",

    # ── Pass 1b: chain consolidation — JSON in, JSON out, MERGE ONLY ────────
    # Vrne samo načrt združevanja, ne celotnega dokumenta. Združevanje izvede koda.
    "claim_consolidation": """You are reviewing an argument extraction for over-splitting.

Extraction reads the transcript in order and splits too eagerly: it emits a new entry
every time the speaker changes topic, even when they are still supporting the SAME
conclusion. Your job is to say WHICH entries belong together. You do not rewrite
anything — you only return a plan; the merging itself is done mechanically.

Find FOUR patterns. Check every entry against all four.

PATTERN 1 — CHAIN: the conclusion of one entry is a stepping stone for the next,
building toward one final thesis ("everything with parts has potency" -> "whatever has
potency needs an external cause" -> ... -> "therefore God exists"). The tell: an
intermediate conclusion is asserted only so the NEXT entry can use it.

PATTERN 2 — RESTATEMENT: the same conclusion stated twice, usually once near the start
and once near the end ("Monarchy is better than democracy" / "Monarchy is far better
than democracy or republicanism"). One argument, however far apart the entries sit.

PATTERN 3 — PARALLEL SUPPORT: several entries are examples, historical cases or
statistics supporting ONE underlying claim ("Germany after the Kaiser...", "Russia
after the Tsar...", "then China and Cambodia..."). One argument whose premises are
those cases. The tell: they all answer the same question.

PATTERN 4 — THIN ENTRY: an entry carrying 0 or 1 premises is rarely a position of
its own. One reason is usually a reason FOR something else the speaker is arguing.
Look for the entry whose conclusion that lone reason actually supports and absorb the
thin entry there. Watch especially for a single premise that merely restates the
entry's own conclusion in other words — that entry has no support at all and belongs
with its neighbour. EVERY thin entry must end up somewhere: absorb it into the entry
its reason actually supports, or, when no entry fits, put it in `drop`. Never leave a
thin entry standing on its own — an entry the speaker backed with fewer than two
reasons is not reported as an argument. Do NOT invent a second premise to save it;
you return a plan, not text.

DROP these entirely — they are not arguments about the topic:
  • META-COMMENTARY about the argument itself ("this argument is undefeatable",
    "it has convinced thousands", the speaker's track record with it)
  • BARE ASSERTIONS with no reasoning: an empty premise list that nothing supports
  • INSULTS, mockery, interpersonal jabs

RULES:
  • Use arg_id values EXACTLY as they appear in the input.
  • Each arg_id may appear at most ONCE in the whole plan.
  • `keep` is the entry whose conclusion is the fullest statement of the merged point;
    the others are absorbed into it as premises.
  • A group needs at least two entries. Never invent arg_ids.
  • If nothing needs merging or dropping, return empty lists.

SELF-CHECK: a speech that yields 20 or more entries almost always contains several
groups. If your plan is empty for such a list, re-read it and ask for each pair:
"do these two answer the same question?"

SECOND SELF-CHECK: scan the input for entries with 0 or 1 premises. Each one is either
a thin entry to absorb (pattern 4) or a drop. It is a real result only when its
conclusion is clearly a claim of its own that nothing else covers. NEVER invent
support to make a thin entry look better — you return a plan, not text.

INPUT FORMAT: a JSON array of entries, each with `id` (use these verbatim),
`speaker`, `conclusion` (the argument's conclusion) and `premises` (how many
premises it has — an entry with 0 premises and no support anywhere is a candidate
for `drop`).

Return ONLY this JSON:
{{
  "merges": [
    {{
      "keep": "arg_id of the entry to keep",
      "absorb": ["arg_id", "arg_id"],
      "pattern": "chain|restatement|parallel_support|thin_entry",
      "reason": "one short sentence"
    }}
  ],
  "drop": [
    {{"arg_id": "arg_id", "reason": "meta_commentary|bare_assertion|insult"}}
  ]
}}

ARGUMENTS:
{claims}""",

    "argument_structure": """Assess each extracted argument below: does its conclusion follow from its premises,
and does its reasoning contain a named logical fallacy?

{prev_pass}

HOW TO READ THE INPUT:
Each argument carries an arg_id, the speaker, the position being defended, and the
numbered premises given for it. Everything you need is in the argument itself — you are
NOT looking at a transcript and you must not refer to one.

EVERY judgement you return MUST name the arg_id it belongs to, exactly as written above.

DO NOT REPORT ON THE EXCHANGE. You have the extracted arguments and nothing else — no
transcript, no ordering, no record of who answered whom. Whether an argument was
rebutted, how the speaker defended it and whether it survived cannot be read off two
lists of arguments, and guessing it here would put an invented account of the debate
next to a real one. A later pass reads the transcript and maps the exchange.

You return ONE thing: a fallacy entry wherever the reasoning carries a NAMED defect.
Most arguments have none, and returning none for them is the correct result. You do
not rate arguments that are free of named defects, and you do not grade the ones that
are not.

FALLACY & ERROR DETECTION:
Your job is to find REAL problems in how speakers argue. Be THOROUGH but ACCURATE.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FOUNDATIONAL RULE — UNDERSTAND WHAT THE SPEAKER IS TRYING TO DO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Before flagging ANY fallacy, first answer internally:
  "What is this speaker actually trying to say, and what argumentative move are they making?"

If they are DEFENDING A POSITION that is the legitimate subject of the debate (moral,
normative, policy, value-based), that is NOT a fallacy — that is the POINT of debating.
A speaker saying "X is morally wrong" in a debate ABOUT X is not committing a factual
error; they are staking their position. Judge the REASONING that supports that position,
not the position itself.

DO NOT FLAG AS FALLACY:
- Taking a controversial moral / policy / value stance (that IS the debate)
- Appealing to a philosophical, religious, or ethical framework as a premise (that is a
  starting axiom, not a fallacy — even if you disagree with the framework)
- Defending a minority or unpopular view — unpopularity ≠ fallacy
- Strong normative claims ("we ought to...", "X is wrong") backed by a reason
- Factual claims that the speaker happens to get wrong — those are factual errors, not
  logical fallacies (they belong to the fact-checker, not here)

ONLY FLAG A FALLACY WHEN THE ERROR IS IN THE *STRUCTURE* OF THE REASONING — the argument
itself is malformed, regardless of whether you agree with the conclusion.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WHAT TO LOOK FOR (actively hunt for these):
1. STRAWMAN: Speaker misrepresents what the opponent said, then attacks the misrepresentation
2. AD HOMINEM: Character attack IS the argument (not just insults alongside real arguments)
3. FALSE DILEMMA: Presenting only 2 options when more exist
4. CIRCULAR REASONING: Conclusion assumes what it's trying to prove
5. RED HERRING: Changing the subject to avoid the point
6. APPEAL TO AUTHORITY: "X said it, therefore it's true" without substance
7. WHATABOUTISM / TU QUOQUE: Responding to criticism by pointing to something else
8. CHERRY PICKING: Selectively using evidence while ignoring contradicting data
9. SLIPPERY SLOPE: Claiming one thing will inevitably lead to extreme consequences without justification
10. MOVING GOALPOSTS: Changing the criteria after the original point was addressed
11. NON SEQUITUR: Conclusion doesn't follow from the reasoning given
12. EQUIVOCATION: Using the same word with different meanings to mislead
13. HASTY GENERALIZATION: Broad conclusion from one or two examples
14. FALSE CAUSE (post hoc): Treating sequence or correlation as proof of causation
15. APPEAL TO EMOTION: Emotional pressure REPLACES the argument (fear, pity, outrage with no reasoning behind it)
16. LOADED QUESTION: Question that smuggles in an unproven accusation ("Why do you keep lying about X?")
17. MOTTE-AND-BAILEY: Defending a bold claim, then retreating to a trivial version when challenged, as if they were the same claim

WHAT IS NOT A FALLACY:
- Defending a debate position (normative, moral, policy) — that is the debate itself
- A factually mistaken claim — that is an error of fact, not of reasoning
- Strong opinions backed by reasoning
- Repetition (usually signals opponent is evading)
- Emotional language with substance behind it
- Sarcasm or irony (rhetorical devices, not errors)
- Debating aggressively or persistently
- Citing a RELEVANT expert on a question inside their expertise (legitimate evidence,
  not appeal to authority — the fallacy needs missing substance or irrelevant authority)
- A slippery-slope WARNING where the speaker argues the causal mechanism step by step
  (only unjustified inevitability is fallacious)
- An analogy that the speaker explicitly qualifies — imperfect analogies are normal
  argumentation, not automatic false equivalence

NO QUOTA — REPORT WHAT IS ACTUALLY THERE:
A heated political debate may genuinely contain many fallacies; a careful academic
exchange may contain none. Do not pad the list to look thorough, and do not skip
clear cases to look charitable. Every entry must survive the question: "Can I point
to the exact words and name the structural failure?" If not, leave it out or mark
it DEBATABLE.

FALLACY NAMES — USE EXACTLY THESE (CRITICAL FOR CONSISTENCY):
The `type` field must contain one name from this closed list, verbatim and lowercase.
Free-form naming makes the same fallacy appear under many labels across runs
("false cause" / "false cause post hoc" / "post hoc correlation") and destroys any
comparison between analyses.

FORMAL (errors in the shape of the inference itself):
  affirming_the_consequent    denying_the_antecedent    undistributed_middle
  affirming_a_disjunct        illicit_transposition     modal_scope_confusion

INFORMAL (context-dependent):
  ad_hominem              straw_man               false_dilemma
  slippery_slope          appeal_to_authority     appeal_to_emotion
  appeal_to_nature        appeal_to_ignorance     appeal_to_tradition
  appeal_to_popularity    circular_reasoning      whataboutism
  cherry_picking          loaded_question         red_herring
  false_attribution       no_true_scotsman        moving_goalposts
  burden_of_proof_shift   equivocation

WEAK REASONING (the step holds in principle but is too loose):
  hasty_generalization    post_hoc                false_equivalence
  non_sequitur            composition_division    anecdotal_evidence

  other

Guidance for the trickier ones:
  • post_hoc            — "A came before B, therefore A caused B"
  • false_equivalence   — two unlike things treated as comparable
  • cherry_picking      — selecting only the data that fits
  • whataboutism        — deflecting criticism by pointing at the opponent
  • equivocation        — a key term silently shifts meaning mid-argument
  • red_herring         — an irrelevant topic introduced to divert
  • anecdotal_evidence  — a single story offered as proof of a general rule
  • other               — ONLY when no name above fits; then name the failure in
                          `explanation`. Prefer a listed name over `other`.

CATEGORY CALIBRATION (use consistently):
The `category` field says WHAT KIND of failure this is. Use the grouping above:
the name you chose already implies the category, and the two must agree.

  • formal         — the error is in the SHAPE of the inference and is visible
                     without knowing the subject matter. Requires the speaker to
                     have stated an actual deductive step. Example: "If it rained,
                     the ground is wet. The ground is wet, therefore it rained."
                     Rare in speech, because speakers seldom state full syllogisms —
                     but when someone DOES argue deductively, check the form.
  • informal       — the step fails because of context, not shape: the appeal is
                     irrelevant, the opponent's view is distorted, the options are
                     falsely narrowed. The same move can be legitimate elsewhere.
  • weak_reasoning — the inference points the right way but is too loose to carry
                     the conclusion: too small a sample, correlation read as cause,
                     an analogy stretched past what it supports. Not a broken
                     argument, an overreaching one.

Do NOT default to `informal`. If the speaker laid out premises and a conclusion and
the conclusion does not follow from the form, that is `formal`. If the conclusion
follows but is stronger than the evidence licenses, that is `weak_reasoning`.

Every fallacy entry MUST point at the words that carry the flaw: put the position or
the premise it sits in — copied from the input above — in `evidence`. Nothing to point
at → don't flag it. Where the flaw is in a particular premise, give its number in
`premise_index`; where it is in how the premises reach the conclusion as a whole, leave
`premise_index` out.

PART B — RHETORIC ≠ FALLACY — CLASSIFY CORRECTLY:
These are rhetorical DEVICES. Do NOT report them as fallacies when they accompany
real argumentation:
  • Hyperbole and dramatic emphasis        • Rhetorical questions
  • Analogy, metaphor, vivid imagery       • Personal anecdote used as illustration
  • Humor, irony, sarcasm                  • Anaphora / repetition for emphasis
  • Framing and loaded word choice         • Appeals to shared values
The SAME move becomes a fallacy ONLY when it REPLACES the argument (e.g. emotional
appeal with no reasoning = appeal to emotion; anecdote presented as proof of a
general rule = hasty generalization). Ask: "If I strip this device away, is there
still an argument left?" Yes → rhetoric, do not report it. No → consider a fallacy.

Sarcasm and irony are not fallacies either: read the speaker's ACTUAL position, not
the surface words, before deciding whether anything is wrong with the reasoning.

AMBIGUOUS CASES — IMPORTANT:
Sometimes a statement COULD be a fallacy OR a legitimate rhetorical device — it depends on interpretation.
For example: using the Titanic to argue about patriarchy could be a "cherry-picked example" OR a "legitimate
illustrative example" depending on context. In these cases:
- Still include it, but say so in the explanation
- Present BOTH interpretations: "This could be seen as [fallacy] because [...],
  but it could also be interpreted as [legitimate use] because [...]."
- Let the reader decide — your job is to flag it and explain both sides.

Return JSON:
{{
  "fallacies": [
    {{
      "arg_id": "the arg_id of the argument this was found in, exactly as given",
      "premise_index": 0,
      "speaker": "...",
      "type": "one name from the closed list above, verbatim (lowercase, underscores)",
      "category": "formal|informal|weak_reasoning",
      "evidence": "the position or premise that carries the flaw, copied from the input",
      "explanation": "why this is a fallacy, OR if ambiguous: both interpretations"
    }}
  ]
}}""",

    "rebuttal_mapping": """Analyze the debate transcript (provided above) focusing on argumentative exchanges, rebuttals, AND evasion patterns.

ARGUMENTS IDENTIFIED:
{prev_pass}

MODERATOR — CONTEXT, NOT CONTENT:
  • Moderator questions are FACILITATION, not rebuttals. Never list them as rebuttals,
    arguments, or as evasion targets between debaters.
  • Do NOT flag a debater for "evading" a moderator's routine question (evasion only
    counts between debaters — pressure from one side, dodge from the other).
  • DO use moderator content as a BRIDGE when reading the transcript: if the moderator
    summarizes debater A and debater B then responds, B is rebutting A (channeled
    through the moderator). Set "by": "B", "to": "A" — not to the moderator.
  • Use moderator sub-questions to disambiguate WHICH of A's arguments B is responding
    to (so the rebuttal mapping is precise).

INCIDENTAL VOICES (audience, off-camera crew, brief interjections):
  • Ignore irrelevant chatter entirely.
  • If a non-debater raises a substantive point and the debaters engage with it,
    treat the non-debater like a moderator (bridge, not participant). Map any
    rebuttal to the actual debater whose position is being contested, not the
    incidental voice.

Map every significant rebuttal between debaters. For each:
- "target_arg_id": copy the arg_id of the challenged argument from ARGUMENTS IDENTIFIED, VERBATIM.
  This links the rebuttal to the exact argument — get it right.
- "target_claim": copy the exact argument text from ARGUMENTS IDENTIFIED that is being challenged (so it can be matched)
- "rebuttal_content": 1-2 sentences max — just the core of the rebuttal, no long explanation
- "response": 1 sentence — how the original speaker reacted

Record what was said, not who you think won. Whether the argument survived the
exchange is left to the reader.

Rebuttal types: direct_contradiction | undermining_premise | alternative_explanation | questioning_warrant

CRITICALLY — Detect EVASION and NON-ANSWERS:
- When a direct question is asked and the speaker deflects, pivots, or gives a non-answer
- When a speaker repeats the same question — they are NOT getting an answer (the non-answerer is evading)
- When a speaker changes the subject instead of addressing the point raised

Return JSON:
{{
  "rebuttals": [
    {{
      "by": "speaker who makes the rebuttal",
      "to": "speaker whose argument is being rebutted",
      "target_arg_id": "copy the arg_id of the targeted argument from ARGUMENTS IDENTIFIED (verbatim)",
      "target_claim": "exact argument text from ARGUMENTS IDENTIFIED",
      "rebuttal_type": "direct_contradiction|undermining_premise|alternative_explanation|questioning_warrant",
      "rebuttal_content": "1-2 sentence rebuttal — core point only",
      "response": "1 sentence — original speaker's reaction"
    }}
  ],
  "evasions": [
    {{
      "evading_speaker": "who avoided answering",
      "question_asked": "the direct question or challenge in 1 sentence",
      "evasion_type": "deflection|topic_change|non_answer|partial_answer|talked_over",
      "times_asked": 1,
      "explanation": "1-2 sentences — how the speaker avoided answering"
    }}
  ]
}}""",

    # ── MERGED PASS: one transcript read instead of two separate passes ─────
    "synthesis": """You are synthesizing a complete debate analysis from multiple specialized analyses.

CLAIM EXTRACTION: {claims_pass}
ARGUMENT QUALITY: {structure_pass}
REBUTTALS: {rebuttal_pass}
FALLACIES: {fallacy_pass}
FACT-CHECK DATA: {fact_check_data}

This is a 1v1 debate: EXACTLY TWO debaters, taken from the claim extraction. A
moderator, host or audience member is not one of them.

CRITICAL RULES:
- MODERATOR EXCLUSION: If a moderator was present, EXCLUDE them entirely from the
  per-speaker evaluation. The moderator is NOT a debater and must NEVER be assessed
  alongside the two debaters. Their influence on the exchange is reported separately
  in `moderator_influence`.
- NO VERDICT: Do NOT declare a winner and do NOT rank the debaters. Never state that
  one side "won", "dominated", "prevailed" or "made the stronger case", and never
  award a category to either debater. Describe what each side argued and how they
  argued it, and leave the conclusion to the reader. Do NOT rate the quality of
  anyone's case. This applies to EVERY field below, including `summary`.
- DEFENSIVE ROLES ARE LEGITIMATE: A debater may contribute mainly by DEFENCE — making
  few (or even zero) own arguments while dismantling the opponent's case. Describe
  that as what it is. Do NOT treat a low own-argument count as a shortcoming.
- EVASION PATTERNS: Record who avoided answering direct questions from the OTHER
  debater (or from the moderator). Report the dodge, do not grade it.
- DEBATABLE CLAIMS: Not everything is TRUE/FALSE — acknowledge legitimately debatable
  positions.
- Be conservative with fallacy counts — only genuinely clear logical errors.

Return JSON:
{{
  "comparative_evaluation": {{
    "moderator_influence":     {{"present": false, "question_count": 0, "pressed_more": "<debater|balanced|n/a>", "notes": "1-2 sentences on how the moderator's questions shaped the exchange — descriptive only, the moderator is never assessed alongside the debaters"}},
    "per_speaker": {{
      "<debater_name>": {{
        "rhetorical_style": "1 sentence — HOW they argued (tone, structure, use of examples). Describe the style, do not rate it.",
        "factual_accuracy": "1 sentence restating what the fact-check data says about their claims"
      }}
    }},
  }},
  "summary": "4-6 sentence account: the main clash points, what each debater argued and what they rested it on, evasion patterns, and which questions were left open. DESCRIBE, do not judge — no winner, no ranking, no 'stronger case', no 'strengths and weaknesses', no overall verdict."
}}

NOTES ON moderator_influence FIELD:
  • present: true only if an actual moderator / host / interviewer facilitated the debate
  • question_count: how many questions or prompts they put to the debaters
  • pressed_more: the debater who faced the tougher questioning, or "balanced" when even
  • notes: descriptive only — e.g. "framed every question around cost, which favoured X's
    prepared material" or "interrupted Y twice mid-answer". NEVER assess the moderator
    alongside the debaters and never count their questions as rebuttals.""",

    # ── SINGLE-SPEAKER synthesis (solo speech, lecture, interview, reaction) ─
    # One analytical frame for every single-speaker recording: one person reasoning.
    "synthesis_single_speaker": """You are synthesizing a complete analysis of a single speaker.

The recording is a SINGLE-SPEAKER piece — solo speech, lecture, op-ed, interview,
OR a reaction/commentary video where the speaker is responding to external content.
The unifying frame: ONE person making arguments.

ARGUMENT EXTRACTION:    {claims_pass}
ARGUMENT QUALITY:       {structure_pass}
FALLACIES:              {fallacy_pass}
FACT-CHECK DATA:        {fact_check_data}

There is NO opponent in this recording, so there is nothing to compare against.
Describe what the speaker argued. Do NOT rate how good it was.

Return JSON:
{{
  "single_speaker_evaluation": {{
    "unsupported_claims": ["a claim the speaker asserted without giving any reason or evidence for it, in their own words"]
  }},
  "summary": "4-6 sentence descriptive account: what the speaker argues, which positions they take and what they rest them on. No rating, no verdict, no 'strong' or 'weak'."
}}""",
}


# ── LLM PROVIDER ABSTRACTION ───────────────────────────────────────────────

class LLMProvider(ABC):
    """Abstract base — swap between OpenAI and Anthropic.

    Optional kwargs recognised by implementations:
      cached_prefix (str): transcript or other large text to send as a
                           separate, cacheable content block (Anthropic only).
                           OpenAI prepends it to the user message so its own
                           automatic prefix-caching can still activate.
    """

    @abstractmethod
    def call(self, system: str, user: str, temperature: float = 0.1, **kwargs) -> Dict:
        ...

    @abstractmethod
    def provider_name(self) -> str:
        ...


class OpenAIProvider(LLMProvider):
    def __init__(self, model: str = "gpt-4o"):
        from openai import OpenAI
        load_dotenv(BASE_DIR / ".env")
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("Missing OPENAI_API_KEY")
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def call(self, system: str, user: str, temperature: float = 0.1, **kwargs) -> Dict:
        # OpenAI has automatic prompt-caching for repeated prefixes ≥1024 tokens.
        # Prepend cached_prefix so the transcript is at a stable position.
        cached_prefix = kwargs.get("cached_prefix")
        full_user = (cached_prefix + "\n\n" + user) if cached_prefix else user
        max_tokens = kwargs.get("max_tokens")

        request_kwargs = dict(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": full_user},
            ],
            response_format={"type": "json_object"},
            # sampling_kwargs zgladi razlike med generacijami modelov: GPT-5 in
            # o-serija ne sprejmeta temperature in namesto max_tokens
            # zahtevata max_completion_tokens.
            **sampling_kwargs(self.model, temperature, max_tokens),
        )

        response = self.client.chat.completions.create(**request_kwargs)

        choice = response.choices[0]
        return _loads_llm_json(
            choice.message.content or "",
            stop_reason=getattr(choice, "finish_reason", None),
        )

    def provider_name(self) -> str:
        return f"openai/{self.model}"


# Nad tem proračunom izhoda Anthropic zahteva pretočni (streaming) klic.
# Vrednost je konservativna: dovolj visoka, da običajni prehodi ostanejo
# nepretočni, in dovolj nizka, da veliki prehodi ne padejo.
_ANTHROPIC_STREAM_THRESHOLD = 8192


class AnthropicProvider(LLMProvider):
    def __init__(self, model: str = "claude-sonnet-5"):
        try:
            import anthropic
        except ImportError:
            raise ImportError("pip install anthropic")

        load_dotenv(BASE_DIR / ".env")
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("Missing ANTHROPIC_API_KEY in .env")
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def call(self, system: str, user: str, temperature: float = 0.1, **kwargs) -> Dict:
        cached_prefix = kwargs.get("cached_prefix")
        use_cache = cached_prefix and cfg("analysis.prompt_caching", True)

        if use_cache:
            # Send transcript as a separate cached block — Anthropic charges
            # only 10 % of normal input price for cache reads after the first write.
            content = [
                {"type": "text", "text": cached_prefix,
                 "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": user},
            ]
        else:
            content = (cached_prefix + "\n\n" + user) if cached_prefix else user

        # Sistemski poziv se ne predpomni. Vsak korak ima svojega in vsi razen
        # enega so krajši od najmanjše predpone, ki jo Anthropic predpomni.
        max_tokens = kwargs.get("max_tokens", 8192)

        request_kwargs: Dict[str, Any] = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
        )
        # Claude 5 generacija je `temperature` upokojila — klic z nastavljeno
        # vrednostjo vrne 400 "`temperature` is deprecated for this model".
        # Claude 4.x jo še sprejme, zato jo pošljemo samo tem modelom.
        if model_supports_temperature(self.model):
            request_kwargs["temperature"] = temperature

        # Anthropic zahteva pretočni klic, kadar iz max_tokens oceni več kot
        # deset minut. Končni odgovor je enak.
        if max_tokens > _ANTHROPIC_STREAM_THRESHOLD:
            with self.client.messages.stream(**request_kwargs) as stream:
                response = stream.get_final_message()
        else:
            response = self.client.messages.create(**request_kwargs)

        raw = ""
        for block in response.content:
            if hasattr(block, "text"):
                raw += block.text

        return _loads_llm_json(raw, stop_reason=getattr(response, "stop_reason", None))

    def provider_name(self) -> str:
        return f"anthropic/{self.model}"


class GrokProvider(LLMProvider):
    """xAI Grok provider — OpenAI-compatible API at api.x.ai."""

    def __init__(self, model: str = "grok-4.3", reasoning_effort: Optional[str] = None):
        from openai import OpenAI
        load_dotenv(BASE_DIR / ".env")
        api_key = os.getenv("XAI_API_KEY")
        if not api_key:
            raise RuntimeError("Missing XAI_API_KEY")
        self.client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
        self.model = model
        # grok-4.3 supports reasoning_effort: none | low | medium | high.
        # Only used when a Grok model is configured for an analysis pass.
        self.reasoning_effort = (
            reasoning_effort
            if reasoning_effort is not None
            else cfg("analysis.grok_reasoning_effort", "none")
        )

    def call(self, system: str, user: str, temperature: float = 0.1, **kwargs) -> Dict:
        cached_prefix = kwargs.get("cached_prefix")
        full_user = (cached_prefix + "\n\n" + user) if cached_prefix else user
        max_tokens = kwargs.get("max_tokens")

        request_kwargs = dict(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": full_user},
            ],
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        if max_tokens is not None:
            request_kwargs["max_tokens"] = max_tokens
        # extra_body works on every openai-sdk 1.x version (unlike the named
        # reasoning_effort kwarg, which only newer SDKs accept).
        if self.reasoning_effort:
            request_kwargs["extra_body"] = {"reasoning_effort": self.reasoning_effort}

        response = self.client.chat.completions.create(**request_kwargs)

        choice = response.choices[0]
        return _loads_llm_json(
            choice.message.content or "",
            stop_reason=getattr(choice, "finish_reason", None),
        )

    def provider_name(self) -> str:
        return f"grok/{self.model}"


def create_provider(provider_name: str | None = None, model: str | None = None) -> LLMProvider:
    """Factory — create provider from config or explicit args."""
    provider_name = (provider_name or cfg("analysis.provider", "openai")).lower()

    if provider_name == "anthropic":
        model = model or cfg("analysis.model", "claude-sonnet-5")
        return AnthropicProvider(model=model)
    elif provider_name == "grok":
        model = model or cfg("analysis.model", "grok-4.3")
        return GrokProvider(model=model)
    else:
        model = model or cfg("analysis.model", "gpt-4o")
        return OpenAIProvider(model=model)


# ── ANALYSIS ENGINE ─────────────────────────────────────────────────────────

class DebateAnalyzer:
    def __init__(self):
        self.provider = create_provider()
        self.temperature = cfg("analysis.temperature", 0.1)
        self.cache = get_cache()
        self._provider_cache: Dict[str, LLMProvider] = {}
        logger.info("   Analysis provider: %s", self.provider.provider_name())

    # ── PROVIDER HELPERS ─────────────────────────────────────────────────

    def _get_pass_provider(self, pass_name: str) -> LLMProvider:
        """Model za posamezen korak, nastavljiv v analysis.pass_models.

        Sinteza to nastavitev namenoma prezre in vedno teče na glavnem modelu.
        """
        pass_model = cfg(f"analysis.pass_models.{pass_name}", None)
        if pass_model is None:
            return self.provider

        if pass_model in self._provider_cache:
            return self._provider_cache[pass_model]

        try:
            # Auto-detect provider from model name
            if "grok" in pass_model.lower():
                p: LLMProvider = GrokProvider(model=pass_model)
            elif "claude" in pass_model.lower():
                p = AnthropicProvider(model=pass_model)
            else:
                p = OpenAIProvider(model=pass_model)
            self._provider_cache[pass_model] = p
            logger.info("      [pass-model: %s]", pass_model)
            return p
        except Exception as exc:
            logger.warning("   Pass provider %s unavailable: %s", pass_model, exc)
            return self.provider

    def _call_llm(self, system: str, user: str, cache_key: str = "", **kwargs) -> Dict:
        """Call the DEFAULT provider (used by synthesis).

        If the caller doesn't set max_tokens, we set a generous default so that
        _call_provider_json can auto-grow on TruncatedJSONError. Without an
        explicit max_tokens the truncation-retry loop has nothing to ramp up
        from and just re-raises — that bites synthesis hardest because its
        output is the largest of any pass.
        """
        if cache_key:
            cached = self.cache.get(cache_key)
            if cached:
                logger.info("      [cache hit]")
                return cached

        if "max_tokens" not in kwargs:
            kwargs["max_tokens"] = int(cfg("analysis.default_max_tokens", 12288))

        result = self._call_provider_json(
            self.provider,
            system,
            user,
            context="default request",
            **kwargs,
        )

        if cache_key:
            self.cache.set(cache_key, result)
        return result

    def _call_provider_json(
        self,
        provider: LLMProvider,
        system: str,
        user: str,
        *,
        context: str,
        temperature: Optional[float] = None,
        **kwargs,
    ) -> Dict:
        max_attempts = max(1, int(cfg("retry.max_attempts", 3)))
        current_max = kwargs.get("max_tokens")
        hard_cap = max(
            current_max or 0,
            int(cfg("analysis.max_output_tokens_cap", 16384)),
        )
        last_exc: Optional[Exception] = None
        temp = self.temperature if temperature is None else temperature

        for attempt in range(1, max_attempts + 1):
            try:
                return provider.call(system, user, temp, **kwargs)
            except TruncatedJSONError as exc:
                last_exc = exc
                if current_max is None or attempt >= max_attempts:
                    raise

                next_max = min(max(current_max * 2, current_max + 1024), hard_cap)
                if next_max <= current_max:
                    raise

                logger.warning(
                    "   %s output was truncated (attempt %d/%d, max_tokens=%d). Retrying with max_tokens=%d",
                    context,
                    attempt,
                    max_attempts,
                    current_max,
                    next_max,
                )
                current_max = next_max
                kwargs["max_tokens"] = next_max
            except json.JSONDecodeError as exc:
                last_exc = exc
                if attempt >= max_attempts:
                    raise
                logger.warning(
                    "   %s JSON parse failed (attempt %d/%d), retrying: %s",
                    context,
                    attempt,
                    max_attempts,
                    exc,
                )
            except Exception as exc:
                # Prehodne napake ponudnika (Anthropic 529 "Overloaded", 429,
                # prekinjen stream) morajo dobiti nov poskus s kratkim čakanjem —
                # prej so en sam "Overloaded" trenutek pokopale cel prehod.
                # Vsebinske napake (400 invalid_request ...) naprej padejo takoj.
                msg = str(exc).lower()
                transient = any(t in msg for t in
                                ("overloaded", "rate_limit", "rate limit", "429",
                                 "529", "503", "502", "500", "timeout",
                                 "timed out", "connection", "incomplete stream",
                                 "temporarily unavailable", "service unavailable"))
                if not transient or attempt >= max_attempts:
                    raise
                last_exc = exc
                wait = min(2 ** attempt * 2, 30)   # 4 s, 8 s, ...
                logger.warning(
                    "   %s transient provider error (attempt %d/%d), retrying in %ds: %s",
                    context, attempt, max_attempts, wait, str(exc)[:160],
                )
                time.sleep(wait)

        if last_exc:
            raise last_exc
        raise RuntimeError(f"{context} failed without an explicit exception")

    def _call_llm_pass(self, pass_name: str, system: str, user: str,
                       cache_key: str = "",
                       transcript_prefix: Optional[str] = None) -> Dict:
        """Call the pass-specific provider with optional cached transcript prefix.

        transcript_prefix is sent as a separate cacheable content block
        (Anthropic) or prepended to the user message (OpenAI).  This lets
        the transcript be reused across passes without paying full input
        price each time.
        """
        # Auto-invalidate the cache whenever the PROMPT changes. The callers'
        # keys only cover transcript / mode / title, so without this tag a
        # prompt improvement (or an edited house rule) would keep serving
        # results produced by the OLD prompt.
        if cache_key:
            prompt_tag = hashlib.sha256((system + "\x00" + user).encode()).hexdigest()[:8]
            cache_key = f"{cache_key}:{prompt_tag}"
            cached = self.cache.get(cache_key)
            if cached:
                logger.info("      [cache hit]")
                return cached

        provider = self._get_pass_provider(pass_name)
        # Per-pass output budgets. claim_extraction needs more headroom because
        # we now allow arguments to be as long as the reasoning requires. Other
        # structural passes stay cheap; synthesis gets the most headroom.
        pass_max_defaults = {
            "claim_extraction":   8192,  # richer, longer arguments
            # Consolidation returns only a merge PLAN (a few hundred tokens), but
            # the budget must also cover the model's internal reasoning — at 3072
            # that reasoning consumed the whole allowance and the answer came back
            # empty with stop_reason=max_tokens. 8192 leaves ample room and still
            # stays at the streaming threshold, so the call remains a plain one.
            "claim_consolidation": 8192,
            "argument_structure": 8192,
            "rebuttal_mapping":   4096,
            "synthesis":          8192,
        }
        pass_max = int(cfg(f"analysis.pass_max_tokens.{pass_name}",
                           pass_max_defaults.get(pass_name, 4096)))
        # Argument creation must be as REPRODUCIBLE as possible: two runs on
        # the same transcript should not yield 6 arguments once and 12 the
        # next time, so the passes that define the argument set ask for 0.0.
        # Note that the current main model is a Claude 5, which retired
        # `temperature`: the value is dropped in sampling_kwargs and these two
        # passes run at the provider's own setting. The request stays because
        # it costs nothing and does apply the moment a model that accepts it
        # is configured.
        pass_temp = (0.0 if pass_name in ("claim_extraction", "claim_consolidation")
                     else None)
        result = self._call_provider_json(
            provider,
            system,
            user,
            context=f"{pass_name} pass",
            temperature=pass_temp,
            cached_prefix=transcript_prefix,
            max_tokens=pass_max,
        )

        # Validate output structure (auto-repair missing fields)
        from llm_schemas import validate_pass
        result = validate_pass(
            pass_name, result,
            retry_fn=lambda: self._call_provider_json(
                provider,
                system,
                user,
                context=f"{pass_name} schema repair",
                temperature=pass_temp,
                cached_prefix=transcript_prefix,
                max_tokens=pass_max,
            ),
        )

        if cache_key:
            self.cache.set(cache_key, result)
        return result



    # ── MULTI-PASS ──────────────────────────────────────────────────────

    def analyze_multi_pass(self, transcript: str, fact_check_data: Optional[Dict],
                           video_title: str = "",
                           fact_check_fn: Optional[Callable[[Dict], Dict]] = None) -> Dict:
        # Refuse before doing anything: an over-long transcript used to be
        # silently cut to the budget, which dropped the closing statements —
        # in a debate usually the strongest part — while the report still
        # looked complete. A partial analysis that does not say it is partial
        # is worse than no analysis.
        transcript_budget = int(cfg("analysis.transcript_token_budget_chars", 80000))
        if len(transcript) > transcript_budget:
            raise RecordingTooLongError(
                f"transcript {len(transcript)} chars exceeds analysis budget "
                f"{transcript_budget}"
            )

        raw_mode = cfg("pipeline.mode", "debate").lower()
        # ── Mode normalization ─────────────────────────────────────────
        # Dva načina: solo (en govorec) in debate (natanko dva debaterja).
        # Stari vrednosti reaction in debate_1v1 se preslikata vanju.
        if raw_mode == "reaction":
            mode_label = "solo"
        elif raw_mode == "debate_1v1":
            mode_label = "debate"
        elif raw_mode in ("solo", "debate"):
            mode_label = raw_mode
        else:
            mode_label = "debate"   # safe default
        is_solo = mode_label == "solo"
        logger.info("[4] Multi-pass analysis [%s] via %s...", mode_label,
                    self.provider.provider_name())

        working = transcript

        # ── Prompt caching: use the SAME transcript prefix for every pass ──
        # Prepis se pripravi enkrat. Oba koraka, ki ga potrebujeta, dobita
        # iste bajte, sicer predpomnjenje pri Anthropicu odpove.
        tx = working
        tx_prefix = f"--- TRANSCRIPT ---\n{tx}\n--- END ---"

        # Solo skips rebuttal_mapping; reaction and debate_1v1 include it
        if is_solo:
            default_passes = ["claim_extraction", "argument_structure", "synthesis"]
        else:
            # Both reaction and debate_1v1 use rebuttal_mapping (2 speakers)
            default_passes = ["claim_extraction", "argument_structure",
                              "rebuttal_mapping", "synthesis"]
        passes_to_run = cfg("analysis.passes", default_passes)

        import hashlib
        # Hash the FULL transcript, not just the first 5k chars, to avoid
        # cache collisions for different debates that share the same opening
        # (intros, sponsor reads, standard greetings).
        transcript_hash = hashlib.sha256(working.encode()).hexdigest()[:16]
        ptag = self.provider.provider_name().replace("/", "_")
        mtag = mode_label  # include mode in cache keys so solo/debate don't collide

        # NOTE: an earlier version appended a web-researched profile of each speaker
        # (bio, known positions, political leaning) to this system prompt. It was
        # removed: the analysis must judge arguments on what was said in THIS
        # recording, and handing the model a political label for the speaker
        # beforehand works against that — the prompt even had to warn the model not
        # to be biased by the background it had just been given.

        failed_passes: List[str] = []
        # Why each pass failed, so a half-empty analysis can be diagnosed from
        # the stored output instead of guessing. Kept alongside passes_failed.
        failure_reasons: Dict[str, str] = {}

        # ── Pass 1: Claim + premise extraction ──────────────────────
        claims_result: Dict = {}
        if "claim_extraction" in passes_to_run:
            logger.info("   Pass 1: Claim extraction...")
            # Title hint: listicle titles ("9 razlogov za...") fix the expected
            # argument count; any title gives topic context. Include it in the
            # cache key so a changed title doesn't serve a stale extraction.
            title_block = _title_hint_block(video_title)
            title_tag = (hashlib.sha256(video_title.encode()).hexdigest()[:8]
                         if title_block else "nt")
            try:
                claims_result = self._call_llm_pass(
                    "claim_extraction",
                    _system_extraction(mode=mode_label),
                    PASS_PROMPTS["claim_extraction"] + title_block,
                    cache_key=f"p1:{ptag}:{mtag}:{transcript_hash}:{title_tag}",
                    transcript_prefix=tx_prefix,
                )
            except Exception as e:
                logger.error("   Pass 1 FAILED: %s — analysis cannot continue without claims", e)
                raise  # claim_extraction is critical — can't continue without it

        # ── 1v1 guard: refuse rather than analyse the wrong pair ──────
        # The system supports exactly two debaters. Pass 1 flags a mismatch
        # itself (too_many_debaters / too_few_debaters); we also count the
        # extracted speakers as a fallback in case the flag is missing.
        if not is_solo:
            _assert_one_on_one(claims_result)

        # Assign stable, deterministic argument IDs NOW — before passes 1b-4 run —
        # so the annotated arguments are threaded into their input JSON and each
        # later pass can reference an argument by its id rather than by copied text.
        _assign_argument_ids(claims_result)

        # ── Pass 1b: consolidation ───────────────────────────────────
        # Extraction over-splits: a chained derivation, a thesis restated at the
        # end, or a series of examples for one claim all come back as separate
        # entries. The model returns only a MERGE PLAN (a few hundred tokens);
        # the merge itself is executed by code, so it is deterministic, cannot
        # truncate and cannot lose content. Non-fatal: on any failure the
        # analysis continues with the unmerged extraction.
        consolidation_info: Dict[str, Any] = {}
        if claims_result and cfg("analysis.consolidate_arguments", True):
            n_args = sum(
                len(d.get("arguments") or [])
                for d in (claims_result.get("speakers") or {}).values()
                if isinstance(d, dict)
            )
            if n_args > 1:
                logger.info("   Pass 1b: Consolidation plan (%d arguments)...", n_args)
                try:
                    plan = self._call_llm_pass(
                        "claim_consolidation", _CONSOLIDATION_SYSTEM,
                        PASS_PROMPTS["claim_consolidation"].format(
                            claims=_consolidation_input(claims_result)),
                        cache_key=f"p1b:{ptag}:{mtag}:{transcript_hash}",
                    )
                    if not (plan.get("merges") or plan.get("drop")):
                        logger.info("   Consolidation: nothing to merge")
                    consolidation_info = _apply_consolidation_plan(claims_result, plan)
                    if consolidation_info.get("applied"):
                        logger.info("   Consolidated %d → %d arguments (%d group(s), %d dropped)",
                                    consolidation_info.get("arguments_before", n_args),
                                    consolidation_info.get("arguments_after", n_args),
                                    consolidation_info.get("merged_groups", 0),
                                    consolidation_info.get("dropped", 0))
                except Exception as e:
                    logger.warning("   Pass 1b failed (non-critical): %s — keeping original extraction", e)
                    consolidation_info = {"applied": False, "reason": str(e)[:200],
                                          "arguments_before": n_args}
                    failed_passes.append("claim_consolidation")
                    failure_reasons["claim_consolidation"] = str(e)[:300]

        # ── Pass 2: Fallacies, from the arguments alone ──────────────
        # These used to be two passes over the same input, asking the same
        # question in two vocabularies: 44 % of the free-text "issues" the
        # structure pass produced restated a fallacy the fallacy pass had
        # already named. One pass now answers both — does the conclusion follow,
        # and does a named defect explain why not — so the account of what is
        # wrong with an argument exists once, with a closed vocabulary behind it.
        structure_result: Dict = {}
        fallacy_result: Dict = {}
        if "argument_structure" in passes_to_run and claims_result:
            logger.info("   Pass 2: Fallacy detection...")
            try:
                combined = self._call_llm_pass(
                    "argument_structure", _system_fallacies(),
                    PASS_PROMPTS["argument_structure"].format(
                        prev_pass=_compact_json(claims_result.get("speakers", {}))
                    ),
                    cache_key=f"p2v:{ptag}:{mtag}:{transcript_hash}",
                )
                structure_result = {"speakers": combined.get("speakers", {})}
                fallacy_result = {"fallacies": combined.get("fallacies", [])}
            except Exception as e:
                logger.error("   Pass 2 FAILED: %s, continuing without fallacies", e)
                failed_passes.append("argument_structure")
                failure_reasons["argument_structure"] = str(e)[:300]

        # ── Fact-checking, once the arguments exist ─────────────────
        # Checking the transcript before this point spent money on material the
        # extraction then discarded, and left the verdicts unattached to any
        # argument. Given the arguments, the checker works on their premises and
        # every verdict carries the arg_id it belongs to. The caller passes the
        # function in; without it the behaviour is unchanged.
        if fact_check_fn is not None:
            try:
                fact_check_data = fact_check_fn(claims_result.get("speakers") or {})
            except Exception as e:
                logger.error("   Fact-checking FAILED: %s — continuing without it", e)
                failed_passes.append("fact_check")
                failure_reasons["fact_check"] = str(e)[:300]
                fact_check_data = fact_check_data or {}

        # ── Pass 4: Rebuttal & evasion mapping ──────────────────────
        # The only pass besides extraction that needs the transcript: who
        # answered whom, and who dodged, cannot be read off the argument lists.
        rebuttal_result: Dict = {}
        if "rebuttal_mapping" in passes_to_run:
            logger.info("   Pass 4: Rebuttal & evasion mapping...")
            try:
                prompt = PASS_PROMPTS["rebuttal_mapping"].format(
                    prev_pass=_compact_json(claims_result.get("speakers", {})),
                )
                rebuttal_result = self._call_llm_pass(
                    "rebuttal_mapping", _system_rebuttal(), prompt,
                    cache_key=f"p4:{ptag}:{mtag}:{transcript_hash}",
                    transcript_prefix=tx_prefix,
                )
            except Exception as e:
                logger.error("   Pass 4 FAILED: %s — continuing without rebuttals", e)
                failed_passes.append("rebuttal_mapping")
                failure_reasons["rebuttal_mapping"] = str(e)[:300]

        # ── Pass 5: Synthesis ───────────────────────────────────────
        logger.info("   Pass 5: Synthesis (%s)...", mode_label)
        fact_context = self._format_fact_checks(fact_check_data)

        # Solo speeches, lectures and reaction videos share ONE synthesis prompt:
        # the analytical frame is the same — a single speaker's reasoning.
        is_single_speaker = is_solo   # "reaction" normalizes to "solo" upstream
        if is_single_speaker:
            synthesis_prompt = PASS_PROMPTS["synthesis_single_speaker"].format(
                claims_pass      = _fit_compact_json(claims_result, 8000),
                structure_pass   = _fit_compact_json(structure_result, 4000),
                fallacy_pass     = _fit_compact_json(fallacy_result, 4000),
                fact_check_data  = fact_context[:6000],
            )
        else:
            synthesis_prompt = PASS_PROMPTS["synthesis"].format(
                claims_pass    = _fit_compact_json(claims_result, 8000),
                structure_pass = _fit_compact_json(structure_result, 4000),
                rebuttal_pass  = _fit_compact_json(rebuttal_result, 6000),
                fallacy_pass   = _fit_compact_json(fallacy_result, 4000),
                fact_check_data= fact_context[:6000],
            )
        # Synthesis always uses the main (full) provider regardless of pass_models
        synthesis_result: Dict = {}
        try:
            synth_system = _system_synthesis()
            # Cache on the full prompt content: any change in upstream pass
            # output or fact-checks changes the key; identical reruns are free.
            synth_key = ("p5:" + ptag + ":" + mtag + ":"
                         + hashlib.sha256((synth_system + "\x00" + synthesis_prompt)
                                          .encode()).hexdigest()[:16])
            synthesis_result = self._call_llm(synth_system, synthesis_prompt,
                                              cache_key=synth_key)
            # Validate synthesis
            from llm_schemas import validate_pass
            synth_schema = "synthesis_single_speaker" if is_single_speaker else "synthesis"
            synthesis_result = validate_pass(synth_schema, synthesis_result)
        except Exception as e:
            logger.error("   Synthesis FAILED: %s — continuing with partial results", e)
            failed_passes.append("synthesis")
            failure_reasons["synthesis"] = str(e)[:300]

        # ── Merge all passes ─────────────────────────────────────────
        final: Dict = dict(claims_result)

        if structure_result.get("speakers"):
            for speaker, data in structure_result["speakers"].items():
                if speaker in final.get("speakers", {}):
                    final["speakers"][speaker].update(data)

        final["fallacies"]             = fallacy_result.get("fallacies", [])
        final["summary"] = synthesis_result.get("summary", "")

        if is_solo:
            # Single-speaker output. `solo_evaluation` is the alias the UI,
            # the report and the PDF all read.
            ss_eval = synthesis_result.get("single_speaker_evaluation", {})
            final["single_speaker_evaluation"] = ss_eval
            final["solo_evaluation"] = {
                "unsupported_claims": ss_eval.get("unsupported_claims", []),
            }
        else:
            final["rebuttals"]              = rebuttal_result.get("rebuttals", [])
            final["evasions"]               = rebuttal_result.get("evasions", [])
            final["comparative_evaluation"] = synthesis_result.get("comparative_evaluation", {})

        # ── Moderator: merge pass-1 facts with the synthesis' read of influence ──
        # The moderator is never scored and never appears in `speakers`; this
        # block only makes their presence and questions visible to the reader.
        final["moderator"] = _merge_moderator_info(
            (claims_result.get("metadata") or {}).get("moderator"),
            (final.get("comparative_evaluation") or {}).get("moderator_influence"),
        )

        if fact_check_data:
            final["fact_check_integration"] = {
                "claims_checked": fact_check_data.get("total_claims", 0),
            }

        # Wire critiques / rebuttals / fallacies to their argument by stable id
        # (LLM-supplied id when valid, else fuzzy fallback). Done once here so the
        # frontend and the text report can link by id instead of re-matching text.
        _resolve_cross_pass_links(final)

        # Synthesis used to be excluded here, which made `passes_completed`
        # under-report the pipeline (it listed 3 of 4 passes even on a fully
        # successful run). Report every pass that ran and did not fail.
        completed = [p for p in passes_to_run if p not in failed_passes]
        if consolidation_info:
            final["consolidation"] = consolidation_info
        final["analysis_method"]    = "multi_pass"
        final["analysis_mode"]      = mode_label
        final["analysis_provider"]  = self.provider.provider_name()
        final["passes_completed"]   = completed
        if failed_passes:
            final["passes_failed"]  = failed_passes
            final["passes_failed_reasons"] = failure_reasons
            logger.warning("Analysis completed with %d failed pass(es): %s",
                           len(failed_passes), ", ".join(failed_passes))
        return final

    @staticmethod
    def _format_fact_checks(fact_check_data: Optional[Dict]) -> str:
        if not fact_check_data:
            return "\n\n[No fact-check data available]"
        checks = fact_check_data.get("fact_checks", [])
        if not checks:
            return "\n\n[No claims were fact-checked]"

        lines = ["\n\n=== FACT-CHECK RESULTS ===\n"]
        for i, fc in enumerate(checks, 1):
            lines.append(f"{i}. [{fc.get('speaker', '?')}] {fc.get('verdict', '?')}: \"{fc.get('exact_claim', '')}\"")
            if fc.get('explanation'):
                lines.append(f"   Finding: {fc['explanation']}")
            if fc.get('correction'):
                lines.append(f"   Correction: {fc['correction']}")
            lines.append("")

        summary = fact_check_data.get("summary", {})
        if summary:
            v = summary.get('verdict_breakdown', {})
            lines.append(f"SUMMARY: TRUE={v.get('TRUE',0)} FALSE={v.get('FALSE',0)} "
                         f"MISLEADING={v.get('MISLEADING',0)}")
        return "\n".join(lines)


# ── TEXT REPORT ─────────────────────────────────────────────────────────────

# ── CROSS-PASS LINKING (stable argument IDs) ────────────────────────────────
# Vsak argument dobi oznako speaker#index, pripisano v kodi po koraku 1. Po njej
# se ocene, zavrnitve in zmote vežejo nazaj na argument. Razrešitev je na enem mestu.

# \w z re.UNICODE ohrani šumnike cele. Vzorec [a-z0-9]+ je slovenske besede
# lomil na č, š in ž, zato je ohlapno ujemanje na slovenskih prepisih odpovedalo.
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _arg_id(speaker: str, index: int) -> str:
    return f"{speaker}#{index}"


class RecordingTooLongError(RuntimeError):
    """The transcript does not fit the budget the analysis passes can read.

    Raised instead of truncating: a silently shortened transcript produces a
    report that looks whole but never saw the end of the recording.
    """


class UnsupportedDebateFormatError(RuntimeError):
    """Raised when a recording does not fit the supported 1v1 debate format.

    The analyser deliberately refuses instead of guessing which two of several
    participants to compare — a wrong pairing produces a confident but wrong
    report, which is worse for the user than a clear refusal."""

    def __init__(self, message: str, detected: Optional[List[str]] = None):
        super().__init__(message)
        self.detected = detected or []


_MODERATOR_ROLE_TOKENS = ("moderator", "host", "interviewer", "voditelj", "audience", "moderatork")


def _drop_moderators_from_speakers(claims_result: Dict) -> List[str]:
    """Remove anyone the model itself labelled a moderator/host from `speakers`.

    The moderator rule tells the model to keep facilitators out of `speakers`,
    but it occasionally slips one in — usually when the transcript label already
    says "(host)". Dropping them here is safer than refusing the whole analysis:
    the participant role comes from the model's own metadata, so we are not
    guessing, and a genuine three-way debate (no moderator role) still trips the
    1v1 guard below. Returns the names that were dropped."""
    speakers = claims_result.get("speakers") or {}
    roles = ((claims_result.get("metadata") or {}).get("participants") or {})
    if not isinstance(speakers, dict) or not isinstance(roles, dict):
        return []

    dropped = [
        name for name in list(speakers)
        if any(tok in str(roles.get(name, "")).lower() for tok in _MODERATOR_ROLE_TOKENS)
    ]
    # Never strip the debate down to fewer than two participants — if that would
    # happen the roles are unreliable and the guard should speak up instead.
    if dropped and len(speakers) - len(dropped) >= 2:
        for name in dropped:
            speakers.pop(name, None)
        logger.info("   Moderator(s) excluded from speakers: %s", ", ".join(dropped))
        return dropped
    return []


def _assert_one_on_one(claims_result: Dict) -> None:
    """Stop the analysis unless exactly two debaters were found (debate mode).

    Trusts the model's own flags first, then falls back to counting the
    speakers it actually extracted."""
    _drop_moderators_from_speakers(claims_result)
    meta = claims_result.get("metadata") or {}
    speakers = [s for s in (claims_result.get("speakers") or {}) if s]
    detected = [d for d in (meta.get("detected_debaters") or []) if d] or speakers

    if meta.get("too_many_debaters") or len(speakers) > 2:
        raise UnsupportedDebateFormatError(
            "too_many_debaters: found {} debaters ({}). This system analyses "
            "one-on-one debates only.".format(len(detected), ", ".join(detected)),
            detected,
        )
    if meta.get("too_few_debaters") or len(speakers) < 2:
        raise UnsupportedDebateFormatError(
            "too_few_debaters: found {} debater(s) ({}). Use solo mode for a "
            "recording with a single speaker.".format(len(detected), ", ".join(detected) or "none"),
            detected,
        )


# Načrtovalec združevanja dobi SAMO to, kar potrebuje za odločitev: identifikator,
# sklep in število premis. Poln izluščeni JSON je pri 16 argumentih obsegal ~13 000
# tokenov, večinoma premis, ki za odločitev "sodita ta dva skupaj?" ne povedo nič.
_PLANNER_ARG_CHARS = 220


def _consolidation_input(claims_result: Dict) -> str:
    rows = []
    for speaker, data in (claims_result.get("speakers") or {}).items():
        if not isinstance(data, dict):
            continue
        for arg in (data.get("arguments") or []):
            if not isinstance(arg, dict) or not arg.get("arg_id"):
                continue
            text = (arg.get("argument") or "").strip()
            if len(text) > _PLANNER_ARG_CHARS:
                text = text[:_PLANNER_ARG_CHARS - 1] + "…"
            rows.append({"id": arg["arg_id"], "speaker": speaker,
                         "conclusion": text,
                         "premises": len(arg.get("premises") or [])})
    return _compact_json(rows)


# Svoj poziv: pove, da gre za nevtralno razvrščanje že izluščenega gradiva.
# Brez tega pojasnila je model ob seznamu političnih sklepov večkrat vrnil prazen odgovor.
_CONSOLIDATION_SYSTEM = (
    "You are a text-structuring assistant working on the output of an academic "
    "argumentation analysis. You are given a list of argument summaries that a "
    "previous step already extracted from a recorded debate. Your only task is to "
    "say which entries restate or support the same point, so they can be merged.\n\n"
    "The entries describe what a speaker argued. They are not your views and not "
    "claims you are asked to endorse, assess or fact-check — you are organising "
    "them, exactly as an editor groups repeated points in a transcript. Their "
    "subject matter is irrelevant to the task; treat political, historical and "
    "religious content the same as any other.\n\n"
    "Never rewrite, never invent, never split. Return only the grouping plan as "
    "JSON, with no commentary before or after it."
)


# An argument is a conclusion plus the reasons given for it. A single reason is
# almost always a reason FOR something else the speaker argues, so an entry left
# with fewer than this many premises is folded into the position it supports or
# dropped. Enforced in code (see _apply_consolidation_plan), never as a prompt
# quota — a quota would make the model invent the missing premise.
MIN_PREMISES = int(cfg("analysis.min_premises_per_argument", 2))


def _apply_consolidation_plan(claims_result: Dict, plan: Dict) -> Dict:
    """Izvede načrt združevanja. Deterministično.

    Model pove le, kateri argumenti sodijo skupaj. Zlivanje opravi koda, zato je
    ponovljivo in ne more ničesar izgubiti. Neveljavna navodila se preskočijo.
    """
    speakers = claims_result.get("speakers") or {}
    if not isinstance(speakers, dict):
        return {"applied": False, "reason": "no speakers"}

    # arg_id → (speaker, argument dict)
    index: Dict[str, tuple] = {}
    for name, data in speakers.items():
        if not isinstance(data, dict):
            continue
        for arg in (data.get("arguments") or []):
            if isinstance(arg, dict) and arg.get("arg_id"):
                index[str(arg["arg_id"])] = (name, arg)

    used: set = set()
    merged_groups = 0
    absorbed_ids: set = set()

    def _as_premise(arg: Dict) -> List[str]:
        """Zlije en vsrkani argument v natanko eno premiso.

        En vsrkani argument je en razlog, zato postane ena premisa: njegov sklep,
        za njim pa njegove lastne premise v istem nizu. Nič se ne izgubi.
        """
        text = (arg.get("argument") or "").strip()
        own = []
        for prem in (arg.get("premises") or []):
            val = prem.get("premise", "") if isinstance(prem, dict) else str(prem)
            val = (val or "").strip()
            if val:
                own.append(val.rstrip(" .;") )
        if not text:
            # No conclusion of its own: fall back to its premises as one string.
            return ["; ".join(own)] if own else []
        if not own:
            return [text]
        return [f"{text.rstrip(' .')} — {'; '.join(own)}."]

    for group in (plan.get("merges") or []):
        if not isinstance(group, dict):
            continue
        keep_id = str(group.get("keep") or "")
        absorb = [str(a) for a in (group.get("absorb") or []) if a]
        ids = [keep_id] + absorb
        # Every id must exist, be unused and belong to the same speaker. Ids must
        # also be distinct within the group: a plan that names the same argument
        # as both `keep` and `absorb` would otherwise delete it.
        if keep_id not in index or len(absorb) < 1:
            continue
        if len(set(ids)) != len(ids):
            continue
        if any(i not in index or i in used for i in ids):
            continue
        if len({index[i][0] for i in ids}) != 1:
            continue

        keeper = index[keep_id][1]
        extra: List[str] = []
        for aid in absorb:
            extra.extend(_as_premise(index[aid][1]))
        existing = [p if isinstance(p, str) else p.get("premise", "")
                    for p in (keeper.get("premises") or [])]
        seen = {e.strip().lower() for e in existing if e}
        keeper["premises"] = list(keeper.get("premises") or []) + [
            e for e in extra if e.strip().lower() not in seen and not seen.add(e.strip().lower())
        ]
        keeper["consolidated_from"] = absorb
        keeper["consolidation_pattern"] = str(group.get("pattern") or "")
        used.update(ids)
        absorbed_ids.update(absorb)
        merged_groups += 1

    dropped_ids: set = set()
    for item in (plan.get("drop") or []):
        aid = str(item.get("arg_id") or "") if isinstance(item, dict) else str(item)
        if aid in index and aid not in used:
            dropped_ids.add(aid)
            used.add(aid)

    remove = absorbed_ids | dropped_ids
    if remove:
        for name, data in speakers.items():
            if not isinstance(data, dict):
                continue
            kept = [a for a in (data.get("arguments") or [])
                    if not (isinstance(a, dict) and str(a.get("arg_id")) in remove)]
            # never empty a speaker that had arguments — that would be a data loss bug
            if kept or not (data.get("arguments") or []):
                data["arguments"] = kept

    # ── Invariant: a reported argument carries at least MIN_PREMISES premises ──
    # Model pove, kam sodi vnos z eno premiso, koda pa jamči, da noben tak ne
    # ostane. Kvota v pozivu bi model prisilila, da manjkajočo premiso izmisli.
    thin_dropped = 0
    for name, data in speakers.items():
        if not isinstance(data, dict):
            continue
        args = [a for a in (data.get("arguments") or []) if isinstance(a, dict)]
        if not args:
            continue
        kept = [a for a in args if len(a.get("premises") or []) >= MIN_PREMISES]
        # Never empty a speaker: if EVERY entry is thin, the extraction itself
        # failed and dropping all of them would leave a blank report. Keep them
        # and let the count show what happened.
        if not kept:
            logger.warning("   All %d argument(s) of %s carry fewer than %d premises "
                           "— keeping them so the speaker is not left empty",
                           len(args), name, MIN_PREMISES)
            continue
        thin_dropped += len(args) - len(kept)
        data["arguments"] = kept

    after = sum(len(d.get("arguments") or []) for d in speakers.values() if isinstance(d, dict))
    return {"applied": True, "merged_groups": merged_groups,
            "dropped": len(dropped_ids), "thin_dropped": thin_dropped,
            "min_premises": MIN_PREMISES,
            "arguments_before": len(index), "arguments_after": after}


def _merge_moderator_info(pass1: Optional[Dict], synthesis: Optional[Dict]) -> Dict:
    """Combine what pass 1 observed about the moderator with the synthesis' read
    of how they shaped the exchange. Purely descriptive — no scoring."""
    p1 = pass1 if isinstance(pass1, dict) else {}
    sy = synthesis if isinstance(synthesis, dict) else {}

    questions = [q for q in (p1.get("questions") or []) if isinstance(q, str) and q.strip()]
    present = bool(p1.get("present") or sy.get("present") or questions)
    try:
        count = int(p1.get("question_count") or sy.get("question_count") or 0)
    except (TypeError, ValueError):
        count = 0
    count = max(count, len(questions))

    pressed = str(p1.get("pressed_more") or sy.get("pressed_more") or "").strip()
    notes = " ".join(x for x in (str(p1.get("notes") or "").strip(),
                                 str(sy.get("notes") or "").strip()) if x).strip()

    return {
        "present": present,
        "name": str(p1.get("name") or "").strip(),
        "question_count": count if present else 0,
        "questions": questions,
        "pressed_more": pressed if present else "",
        "notes": notes if present else "",
    }


def _assign_argument_ids(claims_result: Dict) -> None:
    """Mutate claims_result in place: give every argument a stable arg_id.
    Deterministic + idempotent — re-running yields identical ids, so a cache
    hit on pass 1 (which stores the un-annotated result) is harmless."""
    for speaker, data in (claims_result.get("speakers") or {}).items():
        if not isinstance(data, dict):
            continue
        for i, arg in enumerate(data.get("arguments") or []):
            if isinstance(arg, dict):
                arg["arg_id"] = _arg_id(speaker, i)


def _norm_tokens(text: str) -> set:
    return set(w for w in _WORD_RE.findall((text or "").lower()) if len(w) > 3)


def _fuzzy_best_arg_id(query: str, args: List[Dict]) -> Optional[str]:
    """Best-matching arg_id for a free-text reference, or None if nothing is
    close enough. Compares the reference against each argument's text AND its
    load-bearing premises (a rebuttal often quotes a premise, not the headline)."""
    q = _norm_tokens(query)
    if not q or not args:
        return None
    q_lower = (query or "").lower()
    best_id, best_score = None, 0.0
    for arg in args:
        if not isinstance(arg, dict):
            continue
        arg_text = arg.get("argument") or arg.get("claim") or ""
        prem_text = " ".join(
            (p.get("premise", "") if isinstance(p, dict) else str(p))
            for p in (arg.get("premises") or [])
        )
        cand = _norm_tokens(f"{arg_text} {prem_text}")
        if not cand:
            continue
        jaccard = len(q & cand) / max(len(q | cand), 1)
        a_lower = arg_text.lower()
        contained = bool(a_lower) and (a_lower[:60] in q_lower or q_lower[:60] in a_lower)
        score = max(jaccard, 0.5 if contained else 0.0)
        if score > best_score:
            best_id, best_score = arg.get("arg_id"), score
    return best_id if best_score >= 0.25 else None


def _valid_arg_id(value: Any, valid_ids: set) -> Optional[str]:
    return value if isinstance(value, str) and value in valid_ids else None


def _resolve_cross_pass_links(final: Dict) -> None:
    """Annotate rebuttals and fallacies with the arg_id of the argument
    they refer to. Trust an LLM-supplied id when it is valid; otherwise fall back
    to fuzzy text matching. Mutates `final` in place. Idempotent."""
    speakers = final.get("speakers") or {}
    args_by_speaker: Dict[str, List[Dict]] = {}
    ids_by_speaker: Dict[str, set] = {}
    for speaker, data in speakers.items():
        if not isinstance(data, dict):
            continue
        args = [a for a in (data.get("arguments") or []) if isinstance(a, dict)]
        args_by_speaker[speaker] = args
        ids_by_speaker[speaker] = {a.get("arg_id") for a in args if a.get("arg_id")}

    # NOTE: an LLM-supplied id is only accepted if it belongs to the CORRECT
    # speaker (rebuttal → the rebutted speaker "to", fallacy → the fallacy's
    # speaker). Validating against the global id set let a confused model link
    # a fallacy to another speaker's argument.

    # 1) rebuttals — the targeted argument belongs to the rebutted speaker ("to").
    for reb in (final.get("rebuttals") or []):
        if not isinstance(reb, dict):
            continue
        target = reb.get("to", "")
        resolved = (_valid_arg_id(reb.get("target_arg_id"), ids_by_speaker.get(target, set()))
                    or _fuzzy_best_arg_id(reb.get("target_claim", ""),
                                          args_by_speaker.get(target, [])))
        reb["target_arg_id"] = resolved or ""

    # 3) zmote: model sam navede arg_id. Koda preveri, da je med argumenti
    #    istega govorca. Ohlapno ujemanje ostane le za starejše analize.
    for fal in (final.get("fallacies") or []):
        if not isinstance(fal, dict):
            continue
        who = fal.get("speaker", "")
        own = ids_by_speaker.get(who, set())
        resolved = (_valid_arg_id(fal.get("arg_id"), own)
                    or _valid_arg_id(fal.get("target_arg_id"), own)
                    or _fuzzy_best_arg_id(fal.get("evidence", ""),
                                          args_by_speaker.get(who, [])))
        fal["target_arg_id"] = resolved or ""


def _match_rebuttals(arg_text: str, target_speaker: str, rebuttals: List[Dict],
                     arg_id: str = "") -> List[Dict]:
    """Find rebuttals that target a specific argument by a specific speaker.
    Prefers the stable arg_id link (resolved once in _resolve_cross_pass_links);
    falls back to word overlap between target_claim and argument text."""
    if not rebuttals:
        return []

    # Fast path — stable id link.
    if arg_id:
        by_id = [r for r in rebuttals if r.get("target_arg_id") == arg_id]
        if by_id:
            return by_id

    if not arg_text:
        return []

    arg_text_lower = arg_text.lower()
    arg_words = set(w for w in arg_text_lower.split() if len(w) > 3)

    matched = []
    for r in rebuttals:
        if r.get("to", "").lower() != target_speaker.lower():
            continue
        tc = r.get("target_claim", "").lower()
        if not tc:
            continue
        tc_words = set(w for w in tc.split() if len(w) > 3)
        if not tc_words:
            continue
        overlap = len(arg_words & tc_words) / max(len(arg_words | tc_words), 1)
        if overlap > 0.25 or tc[:60] in arg_text_lower or arg_text_lower[:60] in tc:
            matched.append(r)

    return matched


def render_text_report(analysis: Dict, fact_check_data: Optional[Dict] = None) -> str:
    lines: List[str] = []
    lines.append(f"# {t('report.title')}")
    lines.append("=" * 80)
    lines.append("")
    lines.append(f"*{t('report.provider')}: {analysis.get('analysis_provider', '?')} | "
                 f"{t('report.method')}: {analysis.get('analysis_method', '?')}*")
    passes = analysis.get("passes_completed", [])
    if passes:
        lines.append(f"*{t('report.passes')}: {', '.join(passes)}*")
    lines.append("")

    # Metadata
    meta = analysis.get("metadata", {})
    lines.append(f"## {t('report.metadata')}")
    lines.append(f"**{t('report.topic')}**: {meta.get('topic', 'Unknown')}")
    for speaker, role in meta.get("participants", {}).items():
        lines.append(f"  - {speaker}: {label('role', role)}")
    mod = analysis.get("moderator") or {}
    if mod.get("present"):
        lines.append("")
        lines.append(f"**{t('report.moderator')}**: {mod.get('name') or '?'} — "
                     f"{mod.get('question_count', 0)} {t('report.moderator_questions')}")
        if mod.get("pressed_more"):
            pressed = mod["pressed_more"]
            # Lahko je ime govorca (pusti ga) ali ena od posebnih vrednosti.
            pressed_txt = (label("pressed_more", pressed)
                           if pressed in ("balanced", "n/a") else pressed)
            lines.append(f"  {t('report.moderator_pressed')}: {pressed_txt}")
        for q in (mod.get("questions") or [])[:10]:
            lines.append(f"  - {q}")
        if mod.get("notes"):
            lines.append(f"  {mod['notes']}")
    lines.append("")
    lines.append("=" * 80)

    # ── PER-SPEAKER ARGUMENT BLOCKS ──────────────────────────────────────────
    all_rebuttals = analysis.get("rebuttals", [])
    all_fact_checks = (fact_check_data or {}).get("fact_checks", []) or []

    for sid, d in analysis.get("speakers", {}).items():
        lines.append("")
        lines.append(f"## {sid}")
        lines.append("")

        if d.get("position"):
            lines.append(f"**{t('report.position')}**: {d['position']}")

        lines.append("")

        # Arguments — premise → argument → rebuttals
        arguments = d.get("arguments", d.get("claims", []))  # backward-compat fallback

        for i, arg in enumerate(arguments, 1):
            if not isinstance(arg, dict):
                lines.append(f"  {i}. {arg}")
                lines.append("")
                continue

            arg_text = arg.get("argument", arg.get("claim", "")).strip()
            arg_type = arg.get("type", "")
            premises = arg.get("premises", [])

            # ── Header: argument number + type badge (premises follow, the
            # derived argument/conclusion is printed BELOW them — the reader
            # sees the building blocks first, then what they derive).
            badge = f"[{label('argument_type', arg_type)}]" if arg_type else ""
            lines.append(f"### {t('report.argument_label')} {i}  {badge}".rstrip())

            # ── Premises first
            if premises:
                lines.append(f"**{t('report.premises_label')}**")
                for p in premises:
                    p_text = p.get("premise", p) if isinstance(p, dict) else p
                    lines.append(f"  • {p_text}")

            # ── Derived argument (conclusion) below the premises
            lines.append(f"**{t('report.derived_argument')}** {arg_text}")

            # ── Verdicts on this argument's own premises. Claims are extracted
            # from the arguments, so each one names the argument it came from —
            # the reader sees straight away when an argument rests on a claim
            # that did not hold up, instead of having to match it by hand
            # against the fact-check list further down.
            checked = [c for c in all_fact_checks
                       if c.get("arg_id") and c.get("arg_id") == arg.get("arg_id")]
            if checked:
                lines.append(f"**{t('report.premise_verdicts')}**")
                for c in checked:
                    verdict = get_verdict_label(c.get("verdict") or "UNVERIFIABLE")["label"]
                    lines.append(f"  • [{verdict}] {c.get('exact_claim','').strip()}")

            # ── Rebuttals on this argument (stable id link, fuzzy fallback)
            matched = _match_rebuttals(arg_text, sid, all_rebuttals, arg.get("arg_id", ""))
            if matched:
                lines.append(f"**{t('report.rebuttals_on_this')}**")
                for r in matched:
                    by = r.get("by", "?")
                    content = r.get("rebuttal_content", "").strip()
                    lines.append(f"  ↩ {by}: {content}")

            lines.append("")

        lines.append("-" * 80)

    # ── EVASIONS ─────────────────────────────────────────────────────────────
    evasions = analysis.get("evasions", [])
    if evasions:
        lines.append("")
        lines.append(f"## {t('report.evasions')}")
        for i, ev in enumerate(evasions, 1):
            times = ev.get("times_asked", 1)
            times_str = f" ×{times}" if times > 1 else ""
            lines.append(
                f"\n{i}. **{ev.get('evading_speaker','?')}** "
                f"[{label('evasion_type', ev.get('evasion_type',''))}]{times_str}"
            )
            lines.append(f"   {t('report.question')}: *{ev.get('question_asked','')}*")
            lines.append(f"   {ev.get('explanation','')}")
        lines.append("")
        lines.append("-" * 80)

    # ── FALLACIES ─────────────────────────────────────────────────────────────
    fallacies = analysis.get("fallacies", [])
    if fallacies:
        lines.append("")
        lines.append(f"## {t('report.fallacies')}")
        for i, f in enumerate(fallacies, 1):
            cat = f.get("category", "")
            lines.append(
                f"\n{i}. **{f.get('speaker','?')} — {label('fallacy', f.get('type',''))}**"
                + (f" [{label('fallacy_category', cat)}]" if cat else "")
            )
            if f.get("evidence"):
                lines.append(f"   \"{f['evidence']}\"")
            if f.get("explanation"):
                lines.append(f"   {f['explanation']}")
        lines.append("")
        lines.append("-" * 80)

    # ── EVALUATION (debate: comparative / solo: individual) ───────────────────
    solo_eval = analysis.get("solo_evaluation", {})
    comp      = analysis.get("comparative_evaluation", {})

    if solo_eval:
        lines.append("")
        unsupported = solo_eval.get("unsupported_claims", [])
        if unsupported:
            lines.append(f"## {t('report.unsupported_claims')}")
            for u in unsupported:
                lines.append(f"  • {u}")

    elif comp:
        # Per-speaker evaluation — each debater described on their own terms.
        # There is deliberately no winner, no ranking and no overall verdict.
        per_speaker = comp.get("per_speaker", {})
        if isinstance(per_speaker, dict) and per_speaker:
            lines.append("")
            lines.append(f"## {t('report.per_speaker')}")
            for name, ev in per_speaker.items():
                if not isinstance(ev, dict):
                    continue
                lines.append(f"\n### {name}")
                if ev.get("rhetorical_style"):
                    lines.append(f"**{t('report.rhetorical_style')}**: {ev['rhetorical_style']}")
                if ev.get("factual_accuracy"):
                    lines.append(f"**{t('report.factual_accuracy')}**: {ev['factual_accuracy']}")

    # ── CROSS-CHECK ───────────────────────────────────────────────────────────
    # ── SUMMARY ───────────────────────────────────────────────────────────────
    lines.append("")
    lines.append(f"## {t('report.summary')}")
    lines.append(analysis.get("summary", "No summary"))


    return "\n".join(lines)
