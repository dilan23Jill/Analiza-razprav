"""Debate Analyzer v2.1 — Multi-pass argumentation analysis."""

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
    """Serialize an object as compact JSON for embedding in LLM prompts."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _fit_compact_json(obj: Any, limit: int) -> str:
    """Skrči objekt na dano dolžino, ne da bi razrezal JSON."""
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
        if not lst:
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
    """The provider returned a message with no text content at all."""


class TruncatedJSONError(json.JSONDecodeError):
    """Raised when the model output looks cut off rather than merely malformed."""


def _looks_truncated_json(text: str, exc: json.JSONDecodeError, stop_reason: Optional[str] = None) -> bool:
    stripped = text.rstrip()
    near_end = exc.pos >= max(len(stripped) - 80, 0)

    if stop_reason in {"max_tokens", "length"}:
        return True
    if "Unterminated string" in exc.msg:
        return True
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
    if stop_reason in {"max_tokens", "length"}:
        raise TruncatedJSONError(
            f"Empty model response — budget exhausted (stop_reason={stop_reason})", raw, 0)
    raise EmptyModelResponseError(
        f"Empty model response (stop_reason={stop_reason})", raw, 0)


# PROMPTS

def _recording_rules(mode: str = "debate_1v1") -> str:
    """Pravila o tem, kdo v posnetku šteje za udeleženca."""
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
        "  • Extract arguments ONLY for the primary speaker. Other voices are CONTEXT, not "
        "content.\n"
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
        "  • The speaker's own commentary on top of a presented argument is a separate "
        "argument.\n"
        "\n"
        "VOICES THAT ARE NOT THE PRIMARY SPEAKER (interviewer, host, audience, original-content "
        "speaker, off-camera crew, brief interjections): treat exactly like a moderator. CONTEXT, "
        "not content. Use to interpret responses; do NOT extract their words as arguments. "
        "If irrelevant chatter (heckles, technical asides) "
        "that the primary speaker doesn't engage with, IGNORE entirely.\n"
        if is_single_speaker else ""
    )

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
        "    • DO NOT add the moderator to `speakers` and DO NOT extract their own arguments. "
        "A moderator is not a debater.\n"
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
        "treat that voice exactly like a moderator: context only, no own arguments — "
        "but use what they said to interpret the debaters' responses.\n"
    )


# SISTEMSKI POZIVI: EDEN NA KLIC

def _system_extraction(mode: str = "debate_1v1") -> str:
    """Korak 1: iz prepisa naredi seznam argumentov."""
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
        "Be rigorous, neutral, structured and precise. Here 'neutral' means UNBIASED: "
        "report what you find exactly where you find it, without softening it to keep the "
        "sides looking balanced and without declaring an overall winner.\n"
        "DESCRIBE, DO NOT GRADE: work strictly from what was actually said in THIS recording. "
        "Do NOT let your own views on the TOPIC (political, religious, ideological, moral) "
        "influence what you report. Your subject is HOW the speakers reasoned, not whether "
        "their position is true, and you do not rate anyone's case or rank the speakers.\n"
        "\n"
        "HOW STRICTLY TO JUDGE:\n"
        "1. CONSERVATIVE FALLACY DETECTION: Not every sharp remark or mild insult is an ad "
        "hominem fallacy. In real debates, speakers use colorful language, sarcasm, and pointed "
        "remarks — these are rhetorical tools, not fallacies, UNLESS the speaker uses them AS A "
        "SUBSTITUTE for addressing the argument. A true ad hominem attacks the PERSON instead of "
        "the ARGUMENT. A speaker who says 'that's ridiculous' and then explains why is NOT "
        "committing a fallacy. Flag a fallacy only when you can point to the premise that "
        "carries it and name the structural failure. A case that can honestly be read either "
        "way is reported as ambiguous, with both readings in the explanation: neither "
        "silently dropped nor asserted as certain.\n"
        "2. A POSITION IS NOT A FALLACY: defending a contested moral, political or value "
        "position is the debate itself. Only how the reasoning for it is built can be "
        "defective.\n"
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
        "Be rigorous, neutral, structured and precise. Here 'neutral' means UNBIASED: "
        "report what you find exactly where you find it, without softening it to keep the "
        "sides looking balanced and without declaring an overall winner.\n"
        "DESCRIBE, DO NOT GRADE: work strictly from what was actually said in THIS recording. "
        "Do NOT let your own views on the TOPIC (political, religious, ideological, moral) "
        "influence what you report. Your subject is HOW the speakers reasoned, not whether "
        "their position is true, and you do not rate anyone's case or rank the speakers.\n"
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
        "Be rigorous, neutral, structured and precise. Here 'neutral' means UNBIASED: "
        "report what you find exactly where you find it, without softening it to keep the "
        "sides looking balanced and without declaring an overall winner.\n"
        "DESCRIBE, DO NOT GRADE: work strictly from what was actually said in THIS recording. "
        "Do NOT let your own views on the TOPIC (political, religious, ideological, moral) "
        "influence what you report. Your subject is HOW the speakers reasoned, not whether "
        "their position is true, and you do not rate anyone's case or rank the speakers.\n"
        "\n"
        "You are writing the summary the reader sees first. Report only what the earlier "
        "steps found; do not introduce arguments, fallacies or verdicts that are not in "
        "the material you were given.\n"
        "\n"
        "Return ONLY valid JSON — no markdown, no commentary outside JSON."
        + t("llm.language_instruction")
    )


# Video-title argument-count hint

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "twenty": 20,
    "en": 1, "ena": 1, "eno": 1, "dva": 2, "dve": 2, "trije": 3, "tri": 3,
    "štirje": 4, "štiri": 4, "stiri": 4, "pet": 5, "šest": 6, "sest": 6,
    "sedem": 7, "osem": 8, "devet": 9, "deset": 10, "enajst": 11, "dvanajst": 12,
    "trinajst": 13, "štirinajst": 14, "petnajst": 15, "dvajset": 20,
}

_LIST_NOUN_RE = (
    r"(?:razlog\w*|argument\w*|način\w*|nacin\w*|dokaz\w*|točk\w*|tock\w*|"
    r"stvar\w*|mit\w*|napak\w*|primer\w*|lekcij\w*|dejst\w*|odgovor\w*|znak\w*|"
    r"resnic\w*|laž\w*|laz\w*|tez\w*|trditv\w*|"
    r"reason\w*|way\w*|point\w*|proof\w*|thing\w*|myth\w*|mistake\w*|"
    r"lesson\w*|fact\w*|example\w*|tip\w*|answer\w*|sign\w*|truth\w*|lie\w*|claim\w*)"
)


def _title_argument_count(title: str) -> int:
    """Detect an announced item count in a listicle-style video title."""
    if not title:
        return 0
    t_low = title.lower()
    words = sorted(_NUMBER_WORDS, key=len, reverse=True)
    num = r"(\d{1,2}|" + "|".join(re.escape(w) for w in words) + r")"
    m = re.search(r"\b" + num + r"\s+(?:naj\w+\s+)?" + _LIST_NOUN_RE, t_low)
    if not m:
        m = re.search(r"\btop\s+(\d{1,2})\b", t_low)
        if not m:
            return 0
    raw = m.group(1)
    n = int(raw) if raw.isdigit() else _NUMBER_WORDS.get(raw, 0)
    return n if 2 <= n <= 30 else 0


def _title_hint_block(title: str) -> str:
    """Instruction block appended to the claim_extraction prompt when the video title is
    known.
    """
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
          "premises": ["mini-argument: load-bearing claim — plus the speaker's own reason for it, if given", "..."]
        }
      ],
      "conclusions": ["final conclusion 1"]
    }
  }
}
""",

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
Your job is to find the REAL defects in how speakers argue: every one that is there,
and nothing that is not.

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

WHAT TO LOOK FOR:
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
to the premise that carries it and name the structural failure?" If not, leave it out or mark
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

    "synthesis": """You are synthesizing a complete debate analysis from multiple specialized analyses.

CLAIM EXTRACTION: {claims_pass}
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
  debater, as the rebuttal pass listed them. Report the dodge, do not grade it.
- DEBATABLE CLAIMS: Not everything is TRUE/FALSE — acknowledge legitimately debatable
  positions.
- FALLACIES: report them exactly as the fallacy pass listed them. Do not add, drop
  or re-grade any.

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

    "synthesis_single_speaker": """You are synthesizing a complete analysis of a single speaker.

The recording is a SINGLE-SPEAKER piece — solo speech, lecture, op-ed, interview,
OR a reaction/commentary video where the speaker is responding to external content.
The unifying frame: ONE person making arguments.

ARGUMENT EXTRACTION:    {claims_pass}
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


# LLM PROVIDER ABSTRACTION

class LLMProvider(ABC):
    """Abstract base — swap between OpenAI and Anthropic."""

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
            content = [
                {"type": "text", "text": cached_prefix,
                 "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": user},
            ]
        else:
            content = (cached_prefix + "\n\n" + user) if cached_prefix else user

        max_tokens = kwargs.get("max_tokens", 8192)

        request_kwargs: Dict[str, Any] = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
        )
        if model_supports_temperature(self.model):
            request_kwargs["temperature"] = temperature

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


# ANALYSIS ENGINE

class DebateAnalyzer:
    def __init__(self):
        self.provider = create_provider()
        self.temperature = cfg("analysis.temperature", 0.1)
        self.cache = get_cache()
        self._provider_cache: Dict[str, LLMProvider] = {}
        logger.info("   Analysis provider: %s", self.provider.provider_name())

    # PROVIDER HELPERS

    def _get_pass_provider(self, pass_name: str) -> LLMProvider:
        """Model za posamezen korak, nastavljiv v analysis.pass_models."""
        pass_model = cfg(f"analysis.pass_models.{pass_name}", None)
        if pass_model is None:
            return self.provider

        if pass_model in self._provider_cache:
            return self._provider_cache[pass_model]

        try:
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
        """Call the DEFAULT provider (used by synthesis)."""
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
                msg = str(exc).lower()
                transient = any(t in msg for t in
                                ("overloaded", "rate_limit", "rate limit", "429",
                                 "529", "503", "502", "500", "timeout",
                                 "timed out", "connection", "incomplete stream",
                                 "temporarily unavailable", "service unavailable"))
                if not transient or attempt >= max_attempts:
                    raise
                last_exc = exc
                wait = min(2 ** attempt * 2, 30)
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
        """Call the pass-specific provider with optional cached transcript prefix."""
        provider = self._get_pass_provider(pass_name)
        if cache_key:
            prompt_tag = hashlib.sha256((system + "\x00" + user).encode()).hexdigest()[:8]
            model_tag = provider.provider_name().replace("/", "_")
            cache_key = f"{cache_key}:{model_tag}:{prompt_tag}"
            cached = self.cache.get(cache_key)
            if cached:
                logger.info("      [cache hit]")
                return cached
        pass_max_defaults = {
            "claim_extraction":   8192,
            "argument_structure": 8192,
            "rebuttal_mapping":   4096,
            "synthesis":          8192,
        }
        pass_max = int(cfg(f"analysis.pass_max_tokens.{pass_name}",
                           pass_max_defaults.get(pass_name, 4096)))
        pass_temp = (0.0 if pass_name == "claim_extraction"
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


    # MULTI-PASS

    def analyze_multi_pass(self, transcript: str, fact_check_data: Optional[Dict],
                           video_title: str = "",
                           fact_check_fn: Optional[Callable[[Dict], Dict]] = None) -> Dict:
        transcript_budget = int(cfg("analysis.transcript_token_budget_chars", 80000))
        if len(transcript) > transcript_budget:
            raise RecordingTooLongError(
                f"transcript {len(transcript)} chars exceeds analysis budget "
                f"{transcript_budget}"
            )

        raw_mode = cfg("pipeline.mode", "debate").lower()
        # Mode normalization
        if raw_mode == "reaction":
            mode_label = "solo"
        elif raw_mode == "debate_1v1":
            mode_label = "debate"
        elif raw_mode in ("solo", "debate"):
            mode_label = raw_mode
        else:
            mode_label = "debate"
        is_solo = mode_label == "solo"
        logger.info("[4] Multi-pass analysis [%s] via %s...", mode_label,
                    self.provider.provider_name())

        working = transcript

        tx = working
        tx_prefix = f"--- TRANSCRIPT ---\n{tx}\n--- END ---"

        if is_solo:
            default_passes = ["claim_extraction", "argument_structure", "synthesis"]
        else:
            default_passes = ["claim_extraction", "argument_structure",
                              "rebuttal_mapping", "synthesis"]
        passes_to_run = cfg("analysis.passes", default_passes)

        import hashlib
        transcript_hash = hashlib.sha256(working.encode()).hexdigest()[:16]
        ptag = self.provider.provider_name().replace("/", "_")
        mtag = mode_label


        failed_passes: List[str] = []
        failure_reasons: Dict[str, str] = {}

        # Pass 1: Claim + premise extraction
        claims_result: Dict = {}
        if "claim_extraction" in passes_to_run:
            logger.info("   Pass 1: Claim extraction...")
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
                raise

        # 1v1 guard: refuse rather than analyse the wrong pair
        if not is_solo:
            _assert_one_on_one(claims_result)

        _assign_argument_ids(claims_result)

        # Invariant: a reported argument carries at least two premises
        _drop_thin_arguments(claims_result)

        # Pass 2: Fallacies, from the arguments alone
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

        # Fact-checking, once the arguments exist
        if fact_check_fn is not None:
            try:
                fact_check_data = fact_check_fn(claims_result.get("speakers") or {})
            except Exception as e:
                logger.error("   Fact-checking FAILED: %s — continuing without it", e)
                failed_passes.append("fact_check")
                failure_reasons["fact_check"] = str(e)[:300]
                fact_check_data = fact_check_data or {}

        # Pass 4: Rebuttal & evasion mapping
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

        # Pass 5: Synthesis
        logger.info("   Pass 5: Synthesis (%s)...", mode_label)
        fact_context = self._format_fact_checks(fact_check_data)

        is_single_speaker = is_solo
        if is_single_speaker:
            synthesis_prompt = PASS_PROMPTS["synthesis_single_speaker"].format(
                claims_pass      = _fit_compact_json(claims_result, 8000),
                fallacy_pass     = _fit_compact_json(fallacy_result, 4000),
                fact_check_data  = fact_context[:6000],
            )
        else:
            synthesis_prompt = PASS_PROMPTS["synthesis"].format(
                claims_pass    = _fit_compact_json(claims_result, 8000),
                rebuttal_pass  = _fit_compact_json(rebuttal_result, 6000),
                fallacy_pass   = _fit_compact_json(fallacy_result, 4000),
                fact_check_data= fact_context[:6000],
            )
        synthesis_result: Dict = {}
        try:
            synth_system = _system_synthesis()
            synth_key = ("p5:" + ptag + ":" + mtag + ":"
                         + hashlib.sha256((synth_system + "\x00" + synthesis_prompt)
                                          .encode()).hexdigest()[:16])
            synthesis_result = self._call_llm(synth_system, synthesis_prompt,
                                              cache_key=synth_key)
            from llm_schemas import validate_pass
            synth_schema = "synthesis_single_speaker" if is_single_speaker else "synthesis"
            synthesis_result = validate_pass(synth_schema, synthesis_result)
        except Exception as e:
            logger.error("   Synthesis FAILED: %s — continuing with partial results", e)
            failed_passes.append("synthesis")
            failure_reasons["synthesis"] = str(e)[:300]

        # Merge all passes
        final: Dict = dict(claims_result)

        if structure_result.get("speakers"):
            for speaker, data in structure_result["speakers"].items():
                if speaker in final.get("speakers", {}):
                    final["speakers"][speaker].update(data)

        final["fallacies"]             = fallacy_result.get("fallacies", [])
        final["summary"] = synthesis_result.get("summary", "")

        if is_solo:
            ss_eval = synthesis_result.get("single_speaker_evaluation", {})
            final["single_speaker_evaluation"] = ss_eval
            final["solo_evaluation"] = {
                "unsupported_claims": ss_eval.get("unsupported_claims", []),
            }
        else:
            final["rebuttals"]              = rebuttal_result.get("rebuttals", [])
            final["evasions"]               = rebuttal_result.get("evasions", [])
            final["comparative_evaluation"] = synthesis_result.get("comparative_evaluation", {})

        final["moderator"] = _merge_moderator_info(
            (claims_result.get("metadata") or {}).get("moderator"),
            (final.get("comparative_evaluation") or {}).get("moderator_influence"),
        )

        if fact_check_data:
            final["fact_check_integration"] = {
                "claims_checked": fact_check_data.get("total_claims", 0),
            }

        _resolve_cross_pass_links(final)

        completed = [p for p in passes_to_run if p not in failed_passes]
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


# TEXT REPORT

# CROSS-PASS LINKING (stable argument IDs)

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _arg_id(speaker: str, index: int) -> str:
    return f"{speaker}#{index}"


class RecordingTooLongError(RuntimeError):
    """The transcript does not fit the budget the analysis passes can read."""


class UnsupportedDebateFormatError(RuntimeError):
    """Raised when a recording does not fit the supported 1v1 debate format."""

    def __init__(self, message: str, detected: Optional[List[str]] = None):
        super().__init__(message)
        self.detected = detected or []


_MODERATOR_ROLE_TOKENS = ("moderator", "host", "interviewer", "voditelj", "audience", "moderatork")


def _drop_moderators_from_speakers(claims_result: Dict) -> List[str]:
    """Remove anyone the model itself labelled a moderator/host from `speakers`."""
    speakers = claims_result.get("speakers") or {}
    roles = ((claims_result.get("metadata") or {}).get("participants") or {})
    if not isinstance(speakers, dict) or not isinstance(roles, dict):
        return []

    dropped = [
        name for name in list(speakers)
        if any(tok in str(roles.get(name, "")).lower() for tok in _MODERATOR_ROLE_TOKENS)
    ]
    if dropped and len(speakers) - len(dropped) >= 2:
        for name in dropped:
            speakers.pop(name, None)
        logger.info("   Moderator(s) excluded from speakers: %s", ", ".join(dropped))
        return dropped
    return []


def _assert_one_on_one(claims_result: Dict) -> None:
    """Stop the analysis unless exactly two debaters were found (debate mode)."""
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


MIN_PREMISES = int(cfg("analysis.min_premises_per_argument", 2))


def _drop_thin_arguments(claims_result: Dict) -> Dict:
    """Odstrani argumente z manj kot MIN_PREMISES premisami."""
    speakers = claims_result.get("speakers") or {}
    if not isinstance(speakers, dict):
        return {"applied": False, "reason": "no speakers"}

    before = sum(len(d.get("arguments") or [])
                 for d in speakers.values() if isinstance(d, dict))
    thin_dropped = 0
    for name, data in speakers.items():
        if not isinstance(data, dict):
            continue
        args = [a for a in (data.get("arguments") or []) if isinstance(a, dict)]
        if not args:
            continue
        kept = [a for a in args if len(a.get("premises") or []) >= MIN_PREMISES]
        if not kept:
            logger.warning("   All %d argument(s) of %s carry fewer than %d premises "
                           "— keeping them so the speaker is not left empty",
                           len(args), name, MIN_PREMISES)
            continue
        thin_dropped += len(args) - len(kept)
        data["arguments"] = kept

    after = sum(len(d.get("arguments") or [])
                for d in speakers.values() if isinstance(d, dict))
    if thin_dropped:
        logger.info("   Dropped %d argument(s) with fewer than %d premises",
                    thin_dropped, MIN_PREMISES)
    return {"applied": True, "thin_dropped": thin_dropped,
            "min_premises": MIN_PREMISES,
            "arguments_before": before, "arguments_after": after}


def _merge_moderator_info(pass1: Optional[Dict], synthesis: Optional[Dict]) -> Dict:
    """Combine what pass 1 observed about the moderator with the synthesis' read of how
    they shaped the exchange.
    """
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
    """Mutate claims_result in place: give every argument a stable arg_id."""
    for speaker, data in (claims_result.get("speakers") or {}).items():
        if not isinstance(data, dict):
            continue
        for i, arg in enumerate(data.get("arguments") or []):
            if isinstance(arg, dict):
                arg["arg_id"] = _arg_id(speaker, i)


def _norm_tokens(text: str) -> set:
    return set(w for w in _WORD_RE.findall((text or "").lower()) if len(w) > 3)


def _fuzzy_best_arg_id(query: str, args: List[Dict]) -> Optional[str]:
    """Best-matching arg_id for a free-text reference, or None if nothing is close enough.
    """
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
    """Annotate rebuttals and fallacies with the arg_id of the argument they refer to."""
    speakers = final.get("speakers") or {}
    args_by_speaker: Dict[str, List[Dict]] = {}
    ids_by_speaker: Dict[str, set] = {}
    for speaker, data in speakers.items():
        if not isinstance(data, dict):
            continue
        args = [a for a in (data.get("arguments") or []) if isinstance(a, dict)]
        args_by_speaker[speaker] = args
        ids_by_speaker[speaker] = {a.get("arg_id") for a in args if a.get("arg_id")}


    for reb in (final.get("rebuttals") or []):
        if not isinstance(reb, dict):
            continue
        target = reb.get("to", "")
        resolved = (_valid_arg_id(reb.get("target_arg_id"), ids_by_speaker.get(target, set()))
                    or _fuzzy_best_arg_id(reb.get("target_claim", ""),
                                          args_by_speaker.get(target, [])))
        reb["target_arg_id"] = resolved or ""

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
    """Find rebuttals that target a specific argument by a specific speaker."""
    if not rebuttals:
        return []

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
            pressed_txt = (label("pressed_more", pressed)
                           if pressed in ("balanced", "n/a") else pressed)
            lines.append(f"  {t('report.moderator_pressed')}: {pressed_txt}")
        for q in (mod.get("questions") or [])[:10]:
            lines.append(f"  - {q}")
        if mod.get("notes"):
            lines.append(f"  {mod['notes']}")
    lines.append("")
    lines.append("=" * 80)

    # PER-SPEAKER ARGUMENT BLOCKS
    all_rebuttals = analysis.get("rebuttals", [])
    all_fact_checks = (fact_check_data or {}).get("fact_checks", []) or []

    for sid, d in analysis.get("speakers", {}).items():
        lines.append("")
        lines.append(f"## {sid}")
        lines.append("")

        if d.get("position"):
            lines.append(f"**{t('report.position')}**: {d['position']}")

        lines.append("")

        arguments = d.get("arguments", d.get("claims", []))

        for i, arg in enumerate(arguments, 1):
            if not isinstance(arg, dict):
                lines.append(f"  {i}. {arg}")
                lines.append("")
                continue

            arg_text = arg.get("argument", arg.get("claim", "")).strip()
            premises = arg.get("premises", [])

            lines.append(f"### {t('report.argument_label')} {i}")

            # Premises first
            if premises:
                lines.append(f"**{t('report.premises_label')}**")
                for p in premises:
                    p_text = p.get("premise", p) if isinstance(p, dict) else p
                    lines.append(f"  • {p_text}")

            # Derived argument (conclusion) below the premises
            lines.append(f"**{t('report.derived_argument')}** {arg_text}")

            checked = [c for c in all_fact_checks
                       if c.get("arg_id") and c.get("arg_id") == arg.get("arg_id")]
            if checked:
                lines.append(f"**{t('report.premise_verdicts')}**")
                for c in checked:
                    verdict = get_verdict_label(c.get("verdict") or "UNVERIFIABLE")["label"]
                    lines.append(f"  • [{verdict}] {c.get('exact_claim','').strip()}")

            # Rebuttals on this argument (stable id link, fuzzy fallback)
            matched = _match_rebuttals(arg_text, sid, all_rebuttals, arg.get("arg_id", ""))
            if matched:
                lines.append(f"**{t('report.rebuttals_on_this')}**")
                for r in matched:
                    by = r.get("by", "?")
                    content = r.get("rebuttal_content", "").strip()
                    lines.append(f"  ↩ {by}: {content}")

            lines.append("")

        lines.append("-" * 80)

    # EVASIONS
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

    # FALLACIES
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
                lines.append(f"   {f['evidence']}")
            if f.get("explanation"):
                lines.append(f"   {f['explanation']}")
        lines.append("")
        lines.append("-" * 80)

    # EVALUATION (debate: comparative / solo: individual)
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

    # CROSS-CHECK
    # SUMMARY
    lines.append("")
    lines.append(f"## {t('report.summary')}")
    lines.append(analysis.get("summary", "No summary"))


    return "\n".join(lines)
