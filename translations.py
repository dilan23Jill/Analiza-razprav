"""
Internationalization (i18n) module for the Debate Analysis Pipeline.
Supports English (en) and Slovenian (sl).

Usage:
    from translations import t, get_language
    print(t("pipeline.title"))           # Uses language from config.yaml
    print(t("pipeline.title", "sl"))     # Forces Slovenian

Two separate things live here:

  t(key)              — UI/report chrome (labels, headings, messages).
  label(group, value) — human-readable names for the CATEGORICAL VALUES the model
                        returns ("straw_man" → "slamnati mož").

The values themselves stay English everywhere: the code matches on them verbatim
and the model is explicitly told to keep them (see llm.language_instruction).
Only the presentation is translated — and it is translated from ONE file,
frontend/src/enumLabels.json, which the React front end imports directly. That is
why the report, the PDF and the interface cannot drift apart.
"""

import json
from pathlib import Path

from config_loader import get as cfg

# ── TRANSLATION DICTIONARY ─────────────────────────────────────────────────

TRANSLATIONS: dict[str, dict[str, str]] = {

    # ── Pipeline (main.py) ──────────────────────────────────────────────

    # ── Debate Analyzer (debate_analyzer.py) ────────────────────────────
    "report.title":                     {"en": "DEBATE ANALYSIS REPORT",
                                         "sl": "POROČILO O ANALIZI DEBATE"},
    "report.provider":                  {"en": "Provider",
                                         "sl": "Ponudnik"},
    "report.method":                    {"en": "Method",
                                         "sl": "Metoda"},
    "report.passes":                    {"en": "Passes",
                                         "sl": "Prehodi"},
    "report.metadata":                  {"en": "Metadata",
                                         "sl": "Metapodatki"},
    "report.topic":                     {"en": "Topic",
                                         "sl": "Tema"},
    "report.position":                  {"en": "Position",
                                         "sl": "Stališče"},
    "report.quality":                   {"en": "Quality",
                                         "sl": "Kakovost"},
    "report.cat_formal":                {"en": "formal",
                                         "sl": "formalna"},
    "report.cat_informal":              {"en": "informal",
                                         "sl": "neformalna"},
    "report.cat_weak_reasoning":        {"en": "weak reasoning",
                                         "sl": "šibko sklepanje"},
    "report.moderator":                 {"en": "Moderator",
                                         "sl": "Moderator"},
    "report.moderator_questions":       {"en": "questions asked",
                                         "sl": "zastavljenih vprašanj"},
    "report.moderator_pressed":         {"en": "Pressed harder",
                                         "sl": "Bolj pritiskal na"},
    "report.evasions":                  {"en": "Evasions & Non-Answers",
                                         "sl": "Izogibanja in neodgovarjanja"},
    "report.question":                  {"en": "Question",
                                         "sl": "Vprašanje"},
    "report.fallacies":                 {"en": "Fallacies",
                                         "sl": "Zmote v sklepanju"},
    "report.per_speaker":               {"en": "By Speaker",
                                         "sl": "Po govorcih"},
    "report.factual_accuracy":          {"en": "Factual Accuracy",
                                         "sl": "Točnost dejstev"},
    "report.summary":                   {"en": "Summary",
                                         "sl": "Povzetek"},
    "report.arguments_assessed":        {"en": "arguments assessed",
                                         "sl": "ocenjenih argumentov"},

    # Argument-per-block labels
    "report.argument_label":            {"en": "Argument",
                                         "sl": "Argument"},
    "report.premises_label":            {"en": "Premises:",
                                         "sl": "Premise:"},
    "report.derived_argument":          {"en": "Derived argument (conclusion):",
                                         "sl": "Izpeljan argument (sklep):"},
    "report.premise_verdicts":          {"en": "Fact-check of these premises:",
                                         "sl": "Preverjanje teh premis:"},
    "report.rebuttals_on_this":         {"en": "Rebuttals:",
                                         "sl": "Ovrženja:"},

    # Solo mode labels
    "report.unsupported_claims":        {"en": "Unsupported Claims",
                                         "sl": "Nepodprte trditve"},

    # Comparative keys
    "report.rhetorical_style":          {"en": "Rhetorical Style",
                                         "sl": "Retorični slog"},

    # ── Fact Checker (fact_checker.py) ──────────────────────────────────

    # Verdict labels
    "verdict.TRUE":                     {"en": "TRUE",
                                         "sl": "RESNIČNO"},
    "verdict.TRUE.short":               {"en": "Verified as accurate",
                                         "sl": "Potrjeno kot točno"},
    "verdict.TRUE.desc":                {"en": "Supported by credible, independent sources.",
                                         "sl": "Podprto z verodostojnimi, neodvisnimi viri."},
    "verdict.PARTIALLY_TRUE":           {"en": "PARTIALLY TRUE",
                                         "sl": "DELNO RESNIČNO"},
    "verdict.PARTIALLY_TRUE.short":     {"en": "Contains accurate and inaccurate elements",
                                         "sl": "Vsebuje točne in netočne elemente"},
    "verdict.PARTIALLY_TRUE.desc":      {"en": "Some factual basis but includes inaccuracies or misleading framing.",
                                         "sl": "Ima določeno dejansko osnovo, vendar vključuje netočnosti ali zavajujoče uokvirjanje."},
    "verdict.MISLEADING":               {"en": "MISLEADING",
                                         "sl": "ZAVAJAJOČE"},
    "verdict.MISLEADING.short":         {"en": "Creates a false impression",
                                         "sl": "Ustvarja napačen vtis"},
    "verdict.MISLEADING.desc":          {"en": "Technically defensible but distorts or omits key context.",
                                         "sl": "Tehnično obranjljivo, vendar popači ali izpušča ključni kontekst."},
    "verdict.FALSE":                    {"en": "FALSE",
                                         "sl": "NERESNIČNO"},
    "verdict.FALSE.short":              {"en": "Contradicted by evidence",
                                         "sl": "V nasprotju z dokazi"},
    "verdict.FALSE.desc":               {"en": "Directly contradicted by credible sources.",
                                         "sl": "Neposredno ovrženo z verodostojnimi viri."},
    "verdict.UNVERIFIABLE":             {"en": "UNVERIFIABLE",
                                         "sl": "NEPREVERLJIVO"},
    "verdict.UNVERIFIABLE.short":       {"en": "Cannot be confirmed or refuted",
                                         "sl": "Ni mogoče potrditi ali ovreči"},
    "verdict.UNVERIFIABLE.desc":        {"en": "Insufficient evidence to confirm or refute.",
                                         "sl": "Nezadostni dokazi za potrditev ali ovrženje."},
    "verdict.ERROR":                    {"en": "ERROR",
                                         "sl": "NAPAKA"},
    "verdict.ERROR.short":              {"en": "Fact-check could not be completed",
                                         "sl": "Preverjanja dejstev ni bilo mogoče zaključiti"},
    "verdict.ERROR.desc":               {"en": "Technical error prevented fact-checking.",
                                         "sl": "Tehnična napaka je preprečila preverjanje dejstev."},

    # ── LLM Language instruction ────────────────────────────────────────
    "llm.language_instruction":         {"en": "",
                                         "sl": "\n\nIMPORTANT: Write ALL human-readable prose in your output in SLOVENIAN (slovenščina). "
                                               "Even when the source transcript is in another language (e.g. English), you MUST translate/paraphrase the content into Slovenian. "
                                               "This applies to EVERY prose field, including: arguments, premises, conclusions, positions, "
                                               "explanations, summaries, descriptions, reasoning, corrections, context, counters, rebuttals, issues, and warrants. "
                                               "EXCEPTIONS — keep these in English / original form, do NOT translate them: "
                                               "(1) JSON keys; "
                                               "(2) short categorical/enum values that the code matches on verbatim — e.g. type, verdict, stance, category (keep values like \"causal\", \"FALSE\", \"debate\"); "
                                               "(3) verbatim quotation fields (key_quotes, exact_claim) must stay in the speaker's ORIGINAL words — a translated quote is no longer a quote."},
}

# ── PUBLIC API ──────────────────────────────────────────────────────────────

def get_language() -> str:
    """Return current language from config (default: 'en')."""
    return cfg("pipeline.language", "en").lower()

def t(key: str, lang: str | None = None, **kwargs) -> str:
    """
    Get translated string.
    Args:
        key:    dot-separated translation key
        lang:   override language (default: from config)
        kwargs: format placeholders, e.g. t("summary.critical_warning", n=3)
    """
    lang = lang or get_language()
    entry = TRANSLATIONS.get(key)
    if entry is None:
        return key  # fallback: return key itself

    text = entry.get(lang) or entry.get("en", key)
    if kwargs:
        text = text.format(**kwargs)
    return text

def get_verdict_label(verdict: str, lang: str | None = None) -> dict:
    """Get translated verdict metadata."""
    lang = lang or get_language()
    v = verdict.upper()
    return {
        "label": t(f"verdict.{v}", lang),
        "short": t(f"verdict.{v}.short", lang),
        "description": t(f"verdict.{v}.desc", lang),
    }


# ── CATEGORICAL VALUE LABELS ────────────────────────────────────────────────
# Shared with the front end: the same JSON file is imported by
# frontend/src/utils/LanguageContext.jsx. One source, three outputs.

_LABELS_PATH = Path(__file__).resolve().parent / "frontend" / "src" / "enumLabels.json"


def _load_labels() -> dict:
    """Read the shared label file. Never raises: a missing or malformed file
    degrades to raw values rather than breaking a finished analysis."""
    try:
        data = json.loads(_LABELS_PATH.read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if not k.startswith("_")}
    except Exception:
        return {}


ENUM_LABELS: dict[str, dict[str, dict[str, str]]] = _load_labels()


def label(group: str, value, lang: str | None = None) -> str:
    """Human-readable name for a categorical value the model returned.

        label("fallacy", "straw_man")   → "slamnati mož"   (sl)
        label("argument_type", "causal") → "vzročni"         (sl)

    Unknown values fall back to the value itself with underscores turned into
    spaces, so an unexpected value is still readable instead of raw. That
    fallback is deliberately silent in production; the unit test is what
    guarantees every value in the closed vocabularies has a real label.
    """
    if value is None:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    lang = lang or get_language()
    entry = (ENUM_LABELS.get(group) or {}).get(raw)
    if entry is None:
        # Case-insensitive second try: schemas store HIGH/MEDIUM upper-cased,
        # some scales lower-cased, and the two overlap ("high").
        for key, val in (ENUM_LABELS.get(group) or {}).items():
            if key.lower() == raw.lower():
                entry = val
                break
    if entry is None:
        return raw.replace("_", " ")
    return entry.get(lang) or entry.get("en") or raw.replace("_", " ")
