"""
Pydantic schemas for validating LLM pass outputs.

Each pass in the multi-pass analysis pipeline returns JSON that must conform
to a specific structure.  These schemas validate + repair outputs:
  - Missing keys get sensible defaults
  - Wrong types are coerced where possible
  - Extra keys are preserved (forward-compat)

Usage:
    from llm_schemas import validate_pass
    validated = validate_pass("claim_extraction", raw_dict)
"""

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


# ── Pass 1: Claim Extraction ─────────────────────────────────────────────────

class ArgumentSchema(BaseModel):
    argument: str = ""
    arg_id: str = ""   # stable cross-pass id (speaker#index), assigned in code after pass 1
    type: str = "factual"
    premises: List[str] = Field(default_factory=list)

    model_config = {"extra": "allow"}

    @field_validator("type")
    @classmethod
    def clamp_type(cls, v: str) -> str:
        valid = {"factual", "normative", "causal", "definitional", "debatable"}
        return v if v in valid else "factual"


class SpeakerClaimsSchema(BaseModel):
    position: str = ""
    arguments: List[ArgumentSchema] = Field(default_factory=list)
    conclusions: List[str] = Field(default_factory=list)

    model_config = {"extra": "allow"}


class MetadataSchema(BaseModel):
    topic: str = ""
    participants: Dict[str, str] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class ClaimExtractionSchema(BaseModel):
    metadata: MetadataSchema = Field(default_factory=MetadataSchema)
    speakers: Dict[str, SpeakerClaimsSchema] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


# ── Pass 2: Argument Structure ────────────────────────────────────────────────

class ArgumentStructureSchema(BaseModel):
    """Zmote, poimenovane iz zaprtega slovarja, vsaka vezana na svoj argument."""

    fallacies: List["FallacySchema"] = Field(default_factory=list)

    model_config = {"extra": "allow"}


# ── Pass 4: Rebuttal Mapping ──────────────────────────────────────────────────

class RebuttalSchema(BaseModel):
    by: str = ""
    to: str = ""
    target_arg_id: str = ""   # cross-pass link to the targeted argument (resolved in code)
    target_claim: str = ""
    rebuttal_type: str = "direct_contradiction"
    rebuttal_content: str = ""
    response: str = ""

    model_config = {"extra": "allow"}


class EvasionSchema(BaseModel):
    evading_speaker: str = ""
    question_asked: str = ""
    evasion_type: str = "non_answer"
    times_asked: int = 1
    explanation: str = ""

    model_config = {"extra": "allow"}


class RebuttalMappingSchema(BaseModel):
    rebuttals: List[RebuttalSchema] = Field(default_factory=list)
    evasions: List[EvasionSchema] = Field(default_factory=list)

    model_config = {"extra": "allow"}


# ── Fallacy vocabulary (returned by pass 2) ─────────────────────────────────────────────────────────

# ── Kanonična imena logičnih zmot ────────────────────────────────────────────
# Model občasno vrne različico imena, zato ga normaliziramo. Imena so razvrščena
# po kategoriji, kar je hkrati podlaga za samodejni popravek kategorije.
_FORMAL_NAMES = {
    "affirming_the_consequent", "denying_the_antecedent", "undistributed_middle",
    "affirming_a_disjunct", "illicit_transposition", "modal_scope_confusion",
}
_INFORMAL_NAMES = {
    "ad_hominem", "straw_man", "false_dilemma", "slippery_slope",
    "appeal_to_authority", "appeal_to_emotion", "appeal_to_nature",
    "appeal_to_ignorance", "appeal_to_tradition", "appeal_to_popularity",
    "circular_reasoning", "whataboutism", "cherry_picking", "loaded_question",
    "red_herring", "false_attribution", "no_true_scotsman", "moving_goalposts",
    "burden_of_proof_shift", "equivocation",
}
_WEAK_NAMES = {
    "hasty_generalization", "post_hoc", "false_equivalence", "non_sequitur",
    "composition_division", "anecdotal_evidence",
}

_FALLACY_NAMES = _FORMAL_NAMES | _INFORMAL_NAMES | _WEAK_NAMES | {"other"}

# ime → kategorija, ki ji ime po definiciji pripada
_NAME_TO_CATEGORY = {
    **{n: "formal" for n in _FORMAL_NAMES},
    **{n: "informal" for n in _INFORMAL_NAMES},
    **{n: "weak_reasoning" for n in _WEAK_NAMES},
}

# Delne oblike → kanonično ime. Preverja se z vsebovanostjo, zato ujame tudi
# daljše opisne variante ("appeal to nature / naturalistic fallacy").
_FALLACY_ALIASES = (
    # formalne
    ("affirming_the_consequent", "affirming_the_consequent"),
    ("affirming_consequent", "affirming_the_consequent"),
    ("converse_error", "affirming_the_consequent"),
    ("denying_the_antecedent", "denying_the_antecedent"),
    ("denying_antecedent", "denying_the_antecedent"),
    ("inverse_error", "denying_the_antecedent"),
    ("undistributed_middle", "undistributed_middle"),
    ("affirming_a_disjunct", "affirming_a_disjunct"),
    ("illicit_transposition", "illicit_transposition"),
    ("modal_scope", "modal_scope_confusion"),
    ("formal_fallacy", "non_sequitur"),
    # ostale
    ("post_hoc", "post_hoc"), ("false_cause", "post_hoc"), ("causal_fallacy", "post_hoc"),
    ("straw", "straw_man"), ("ad_hominem", "ad_hominem"), ("personal_attack", "ad_hominem"),
    ("false_dilemma", "false_dilemma"), ("false_dichotomy", "false_dilemma"),
    ("either_or", "false_dilemma"), ("black_and_white", "false_dilemma"),
    ("slippery", "slippery_slope"),
    ("appeal_to_authority", "appeal_to_authority"), ("authority", "appeal_to_authority"),
    ("appeal_to_emotion", "appeal_to_emotion"), ("emotional_appeal", "appeal_to_emotion"),
    ("loaded_language", "appeal_to_emotion"),
    ("naturalistic", "appeal_to_nature"), ("appeal_to_nature", "appeal_to_nature"),
    ("ignorance", "appeal_to_ignorance"), ("tradition", "appeal_to_tradition"),
    ("popularity", "appeal_to_popularity"), ("bandwagon", "appeal_to_popularity"),
    ("ad_populum", "appeal_to_popularity"),
    ("circular", "circular_reasoning"), ("begging_the_question", "circular_reasoning"),
    ("petitio", "circular_reasoning"),
    ("hasty", "hasty_generalization"), ("sweeping_generalization", "hasty_generalization"),
    ("overgeneral", "hasty_generalization"),
    ("false_equivalen", "false_equivalence"), ("false_analogy", "false_equivalence"),
    ("cherry", "cherry_picking"), ("selective", "cherry_picking"),
    ("whataboutism", "whataboutism"), ("tu_quoque", "whataboutism"),
    ("equivocation", "equivocation"), ("ambiguity", "equivocation"),
    ("non_sequitur", "non_sequitur"),
    ("moving_goalpost", "moving_goalposts"), ("no_true_scotsman", "no_true_scotsman"),
    ("burden_of_proof", "burden_of_proof_shift"), ("shifting_the_burden", "burden_of_proof_shift"),
    ("loaded_question", "loaded_question"), ("complex_question", "loaded_question"),
    ("composition", "composition_division"), ("division", "composition_division"),
    ("red_herring", "red_herring"), ("diversion", "red_herring"),
    ("anecdot", "anecdotal_evidence"), ("false_attribution", "false_attribution"),
    ("misattribut", "false_attribution"),
)


def canonical_fallacy_type(raw: str) -> str:
    """Normalize a free-form fallacy name to the closed vocabulary.

    Unrecognised names are kept verbatim (never discarded) so nothing is lost —
    they simply do not benefit from the normalization."""
    v = (raw or "").strip().lower()
    v = v.replace("-", "_").replace("/", "_").replace(" ", "_")
    while "__" in v:
        v = v.replace("__", "_")
    if v in _FALLACY_NAMES:
        return v
    for needle, canonical in _FALLACY_ALIASES:
        if needle in v:
            return canonical
    return (raw or "").strip()


class FallacySchema(BaseModel):
    speaker: str = ""
    type: str = ""
    category: str = "informal"
    evidence: str = ""
    explanation: str = ""
    # The pass reads the extracted arguments, so the model names the argument the
    # fallacy sits in. `arg_id` is what it returns; `target_arg_id` is the field
    # the report and the UI have always read, and code copies one into the other
    # after checking it belongs to the right speaker.
    arg_id: str = ""
    premise_index: Optional[int] = None
    target_arg_id: str = ""

    model_config = {"extra": "allow"}

    # Ime in kategorijo normaliziramo, ker model vrača različice.
    @field_validator("type")
    @classmethod
    def clamp_fallacy_type(cls, v: str) -> str:
        return canonical_fallacy_type(v)

    @field_validator("category")
    @classmethod
    def clamp_category(cls, v: str) -> str:
        v = (v or "informal").lower().replace("-", "_").replace(" ", "_")
        synonyms = {
            "formal": "formal", "formalna": "formal", "deductive": "formal",
            "informal": "informal", "neformalna": "informal",
            "weak_reasoning": "weak_reasoning", "weak": "weak_reasoning",
            "reasoning": "weak_reasoning", "sibko_sklepanje": "weak_reasoning",
        }
        return synonyms.get(v, "informal")

    @model_validator(mode="after")
    def align_category_with_type(self):
        """Derive the category from the fallacy name when the two disagree.

        The name already determines the kind of failure: affirming the consequent
        is formal, ad hominem is informal, hasty generalization is weak reasoning.
        Leaving the two fields independent produced a silent bias — the category
        validator defaults to `informal` on anything unexpected, so in 96 detections
        `formal` never appeared even though the names sometimes implied it. The name
        is the more reliable of the two, so it wins.
        """
        derived = _NAME_TO_CATEGORY.get(self.type)
        if derived and derived != self.category:
            self.category = derived
        return self


# ── Pass 5: Synthesis ─────────────────────────────────────────────────────────

class SynthesisSchema(BaseModel):
    comparative_evaluation: Dict[str, Any] = Field(default_factory=dict)
    summary: str = ""

    model_config = {"extra": "allow"}


class SingleSpeakerSynthesisSchema(BaseModel):
    """Synthesis for single-speaker analyses — solo speech, lecture, interview
    or reaction video. They share one analytical frame: one person reasoning."""
    single_speaker_evaluation: Dict[str, Any] = Field(default_factory=dict)
    summary: str = ""

    model_config = {"extra": "allow"}


# ── VALIDATION DISPATCH ──────────────────────────────────────────────────────

_SCHEMAS = {
    "claim_extraction": ClaimExtractionSchema,
    "claim_consolidation": ClaimExtractionSchema,  # same shape in, same shape out
    "argument_structure": ArgumentStructureSchema,
    "rebuttal_mapping": RebuttalMappingSchema,
    "synthesis": SynthesisSchema,
    "synthesis_single_speaker": SingleSpeakerSynthesisSchema,
}


def validate_pass(pass_name: str, data: Dict, retry_fn=None) -> Dict:
    """Validate LLM output against schema. Returns validated dict.

    If validation fails critically and retry_fn is provided, calls retry_fn()
    to get a fresh response and validates again (once).
    """
    schema_cls = _SCHEMAS.get(pass_name)
    if not schema_cls:
        return data   # no schema for this pass — pass through

    try:
        validated = schema_cls.model_validate(data)
        return validated.model_dump()
    except Exception as e:
        logger.warning("Pass '%s' validation failed: %s — attempting repair", pass_name, e)

        # Try retry if provided
        if retry_fn:
            try:
                fresh = retry_fn()
                validated = schema_cls.model_validate(fresh)
                logger.info("Pass '%s' retry succeeded", pass_name)
                return validated.model_dump()
            except Exception as e2:
                logger.warning("Pass '%s' retry also failed: %s — using defaults", pass_name, e2)

        # Last resort: create with defaults and merge what we can
        try:
            # Try partial validation — fill missing keys with defaults
            safe_data = {}
            for key in schema_cls.model_fields:
                if key in data:
                    safe_data[key] = data[key]
            validated = schema_cls.model_validate(safe_data)
            return validated.model_dump()
        except Exception:
            # Complete failure — return empty defaults
            logger.error("Pass '%s' could not be validated at all, using empty defaults", pass_name)
            return schema_cls().model_dump()
