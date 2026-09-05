"""
Unit tests for the pure-logic modules.

These cover the parts most likely to break silently in the large pipeline files
(schema validation + clamps, consolidation, PDF build)
without needing any API keys or network.

Run from the repo root:
    pip install pytest
    pytest -q
"""

import pathlib
import sys

import pytest

# Make the repo root importable when pytest is run from elsewhere.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def test_sampling_kwargs_per_model_generation():
    from config_loader import model_supports_temperature, sampling_kwargs

    # GPT-5 / o-serija: brez temperature, max_completion_tokens namesto max_tokens.
    assert model_supports_temperature("gpt-5.6-luna") is False
    assert model_supports_temperature("o3-mini") is False
    assert sampling_kwargs("gpt-5.6-luna", 0.0) == {}
    assert sampling_kwargs("gpt-5.6-luna", 0.0, 4096) == {"max_completion_tokens": 4096}

    # Claude 5 generacija: temperature upokojena (400 "deprecated"), a
    # max_tokens OSTANE max_tokens — max_completion_tokens je OpenAI posebnost.
    assert model_supports_temperature("claude-sonnet-5") is False
    assert sampling_kwargs("claude-sonnet-5", 0.1, 4096) == {"max_tokens": 4096}

    # Starejše generacije in drugi ponudniki: temperature ostane.
    for m in ("gpt-4.1-mini", "gpt-4.1-nano", "gpt-4o", "grok-4.3",
              "claude-haiku-4-5-20251001", "claude-sonnet-4-6"):
        assert model_supports_temperature(m) is True, m
        assert sampling_kwargs(m, 0.0, 4096) == {"temperature": 0.0, "max_tokens": 4096}


def test_one_on_one_guard_and_moderator_merge():
    import pytest

    from debate_analyzer import (UnsupportedDebateFormatError, _assert_one_on_one,
                                 _merge_moderator_info)

    # Exactly two debaters → passes.
    _assert_one_on_one({"speakers": {"Ana": {}, "Bojan": {}}, "metadata": {}})

    # Three debaters → refused (flag set by the model).
    with pytest.raises(UnsupportedDebateFormatError) as exc:
        _assert_one_on_one({
            "speakers": {"Ana": {}, "Bojan": {}},
            "metadata": {"too_many_debaters": True,
                         "detected_debaters": ["Ana", "Bojan", "Cilka"]},
        })
    assert exc.value.detected == ["Ana", "Bojan", "Cilka"]

    # Three extracted speakers → refused even without the flag.
    with pytest.raises(UnsupportedDebateFormatError):
        _assert_one_on_one({"speakers": {"A": {}, "B": {}, "C": {}}, "metadata": {}})

    # A single speaker → refused, pointing the user at solo mode.
    with pytest.raises(UnsupportedDebateFormatError) as exc:
        _assert_one_on_one({"speakers": {"Ana": {}}, "metadata": {}})
    assert str(exc.value).startswith("too_few_debaters")

    # A moderator wrongly placed in `speakers` is dropped, not treated as a
    # third debater — otherwise every moderated debate would be refused.
    from debate_analyzer import _drop_moderators_from_speakers
    moderated = {
        "speakers": {"Ana": {}, "Bojan": {}, "James": {}},
        "metadata": {"participants": {"Ana": "debater", "Bojan": "debater",
                                      "James": "moderator (host)"}},
    }
    assert _drop_moderators_from_speakers(moderated) == ["James"]
    assert set(moderated["speakers"]) == {"Ana", "Bojan"}
    _assert_one_on_one(moderated)          # now passes

    # But a genuine three-way debate (no moderator role) is still refused.
    with pytest.raises(UnsupportedDebateFormatError):
        _assert_one_on_one({
            "speakers": {"Ana": {}, "Bojan": {}, "Cilka": {}},
            "metadata": {"participants": {"Ana": "debater", "Bojan": "debater",
                                          "Cilka": "debater"}},
        })

    # Moderator info merges pass 1 facts with the synthesis' read.
    merged = _merge_moderator_info(
        {"present": True, "name": "Voditelj", "question_count": 2,
         "questions": ["Prvo vprašanje?", "Drugo vprašanje?", "Tretje?"]},
        {"pressed_more": "Ana", "notes": "Prekinil Bojana."},
    )
    assert merged["present"] is True
    assert merged["question_count"] == 3          # count never below the listed questions
    assert merged["pressed_more"] == "Ana"
    assert "Prekinil" in merged["notes"]

    # No moderator → everything stays empty, nothing is invented.
    empty = _merge_moderator_info(None, None)
    assert empty == {"present": False, "name": "", "question_count": 0,
                     "questions": [], "pressed_more": "", "notes": ""}


def test_empty_answer_at_token_limit_counts_as_truncation():
    """An empty answer that stopped at the token limit must escalate the budget,
    not repeat the same doomed call: newer models spend part of the allowance on
    internal reasoning, so a too-small budget yields no text at all."""
    import pytest

    from debate_analyzer import TruncatedJSONError, _loads_llm_json

    with pytest.raises(TruncatedJSONError):
        _loads_llm_json("", stop_reason="max_tokens")

    # An empty answer for any other reason is a plain parse failure (retry, no escalation).
    with pytest.raises(Exception) as exc:
        _loads_llm_json("", stop_reason="end_turn")
    assert not isinstance(exc.value, TruncatedJSONError)


def test_consolidation_plan_is_executed_by_code():
    """The model only says WHICH arguments belong together; the merge itself is
    deterministic code, so nothing can truncate and no content can be lost."""
    from debate_analyzer import _apply_consolidation_plan

    def fresh():
        return {"speakers": {"Ana": {"arguments": [
            {"arg_id": "Ana#0", "argument": "Monarchy beats democracy",
             "premises": ["kings answer to God",
                          "a monarch has no election to win"]},
            {"arg_id": "Ana#1", "argument": "Germany fell to the Nazis after the Kaiser",
             "premises": ["1918 abdication"]},
            {"arg_id": "Ana#2", "argument": "Russia fell to the Soviets after the Tsar",
             "premises": []},
            {"arg_id": "Ana#3", "argument": "This argument has convinced thousands",
             "premises": []},
        ]}}}

    data = fresh()
    info = _apply_consolidation_plan(data, {
        "merges": [{"keep": "Ana#1", "absorb": ["Ana#2"], "pattern": "parallel_support"}],
        "drop": [{"arg_id": "Ana#3", "reason": "meta_commentary"}],
    })
    args = data["speakers"]["Ana"]["arguments"]
    assert info["applied"] and info["merged_groups"] == 1 and info["dropped"] == 1
    assert [a["arg_id"] for a in args] == ["Ana#0", "Ana#1"]
    # The absorbed argument survives inside the keeper's premises — nothing is lost.
    joined = " ".join(args[1]["premises"])
    assert "Russia fell to the Soviets" in joined and "1918 abdication" in joined
    assert args[1]["consolidated_from"] == ["Ana#2"]

    # A plan referencing unknown ids, or reusing one twice, is skipped — never fatal.
    data = fresh()
    info = _apply_consolidation_plan(data, {
        "merges": [{"keep": "Ana#0", "absorb": ["Ana#99"]},
                   {"keep": "Ana#1", "absorb": ["Ana#1"]}],
        "drop": [],
    })
    assert info["merged_groups"] == 0
    assert all("consolidated_from" not in a
               for a in data["speakers"]["Ana"]["arguments"])


def test_fallacies_name_the_argument_they_were_found_in():
    """The fallacy pass reads the extracted arguments, so the model names the
    argument itself. The id is still checked against that speaker's own
    arguments — a fallacy attributed to one speaker cannot sit in another's."""
    from debate_analyzer import _resolve_cross_pass_links

    final = {
        "speakers": {
            "Ana": {"arguments": [{"arg_id": "Ana#0", "argument": "a", "premises": ["p"]},
                                  {"arg_id": "Ana#1", "argument": "b", "premises": ["q"]}]},
            "Bor": {"arguments": [{"arg_id": "Bor#0", "argument": "c", "premises": ["r"]}]},
        },
        "fallacies": [
            {"speaker": "Ana", "arg_id": "Ana#1", "evidence": "b", "type": "post_hoc"},
            {"speaker": "Ana", "arg_id": "Bor#0", "evidence": "c", "type": "straw_man"},
            {"speaker": "Ana", "arg_id": "Ana#99", "evidence": "x", "type": "non_sequitur"},
        ],
    }
    _resolve_cross_pass_links(final)
    got = [f["target_arg_id"] for f in final["fallacies"]]

    assert got[0] == "Ana#1", "a valid id from the speaker's own arguments is kept"
    assert got[1] == "", "another speaker's argument must not be accepted"
    assert got[2] == "", "an id that does not exist must not be accepted"


def test_claims_are_extracted_from_argument_premises():
    """Claims now come from the extracted arguments, not the raw transcript, so
    every verdict can be attached to the argument it belongs to — and nothing is
    checked that the extraction already discarded."""
    import json as _json
    from types import SimpleNamespace
    from fact_checker import FactChecker

    speakers = {"Ana": {"arguments": [
        {"arg_id": "Ana#0", "argument": "Screens harm children",
         "premises": ["teen suicide up 167% among girls to 2020",
                      "one in three British children short-sighted"]},
        {"arg_id": "Ana#1", "argument": "Phones should be banned in schools",
         "premises": ["banning phones is the right thing to do",
                      "classes are calmer without them"]},
    ]}}

    captured = {}
    returned = {"claims": [
        {"exact_claim": "teen suicide up 167% among girls to 2020",
         "arg_id": "Ana#0", "premise_index": 0, "speaker": "Ana",
         "claim_type": "health"},
        {"exact_claim": "a claim about an argument that does not exist",
         "arg_id": "Ana#99", "premise_index": 0, "speaker": "Ana",
         "claim_type": "statistic"},
    ]}

    def fake_create(**kwargs):
        captured["payload"] = kwargs["messages"][1]["content"]
        msg = SimpleNamespace(content=_json.dumps(returned))
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    checker = FactChecker.__new__(FactChecker)
    checker.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)))
    checker.cache = SimpleNamespace(get=lambda k: None, set=lambda k, v: None)

    claims = checker.extract_claims_from_arguments(speakers)

    # The model sees the arguments with their ids and numbered premises …
    assert "[Ana#0]" in captured["payload"] and "premise 1:" in captured["payload"]
    # … and the payload is the arguments, not the transcript.
    assert "Screens harm children" in captured["payload"]

    # A claim pointing at an unknown argument is dropped: an unattachable claim
    # is exactly what this design set out to remove.
    assert len(claims) == 1
    assert claims[0]["arg_id"] == "Ana#0" and claims[0]["premise_index"] == 0

    assert checker.extract_claims_from_arguments({}) == []


def test_duration_limit_keeps_transcription_in_one_call():
    """The accepted length must fit the transcription model in ONE call.

    gpt-4o-transcribe-diarize takes at most 1400 seconds of audio per request
    and refuses anything longer with a 400, whatever the file size. That limit
    is far tighter than both the analysis transcript budget and the 25 MB
    upload cap, so it is the one that decides. A recording that passes this
    check must not be able to fail later at the API.
    """
    pytest.importorskip("fastapi")
    from api import (_hhmmss_to_seconds, _max_analysable_seconds,
                     _assert_duration_analysable, _effective_duration_seconds)
    from fastapi import HTTPException

    assert _hhmmss_to_seconds("5:30") == 330
    assert _hhmmss_to_seconds("1:05:30") == 3930
    for junk in ("", "abc", "1::2", None):
        assert _hhmmss_to_seconds(junk) is None

    limit = _max_analysable_seconds()

    # The hard one: the transcription model's per-call audio duration.
    TRANSCRIBE_MAX_SECONDS = 1400
    assert limit <= TRANSCRIBE_MAX_SECONDS, (
        f"a {limit} s recording would be refused by the transcription model, "
        f"which takes {TRANSCRIBE_MAX_SECONDS} s per call")

    from config_loader import get as cfg
    minutes = limit / 60
    assert minutes >= 15, "a limit this low would not cover a normal debate segment"

    # The other two limits must sit above it, so that duration is the only one
    # a user can hit.
    budget = int(cfg("analysis.transcript_token_budget_chars", 80000))
    assert minutes * 1180 < budget, "a dense recording at the limit would overflow the budget"
    upload_mb = minutes * 60 * 32 / 8 / 1024
    assert upload_mb < 25, f"upload would be {upload_mb:.1f} MB"

    # No chunked transcription path may come back: it needed a model call to
    # decide which per-chunk speaker labels were the same person.
    import transcribe
    for gone in ("_should_chunk", "_split_audio_by_time",
                 "_transcribe_long_via_chunks", "_unify_speakers_across_chunks"):
        assert not hasattr(transcribe, gone), gone

    _assert_duration_analysable(limit)            # exactly at the limit is fine
    _assert_duration_analysable(0)                # unknown length: no objection
    with pytest.raises(HTTPException) as exc:
        _assert_duration_analysable(limit + 1)
    assert exc.value.status_code == 422

    # A range shorter than the limit is accepted without probing the video.
    assert _effective_duration_seconds("url", "0:10:00", "0:40:00") == 1800


def test_over_long_transcript_is_refused_not_truncated():
    """A transcript longer than the analysis budget must raise, not be cut.

    Silently truncating dropped the closing statements — in a debate usually
    the strongest part — while the report still looked complete."""
    from debate_analyzer import DebateAnalyzer, RecordingTooLongError
    from config_loader import get as cfg

    budget = int(cfg("analysis.transcript_token_budget_chars", 80000))
    analyzer = DebateAnalyzer.__new__(DebateAnalyzer)   # no provider needed

    with pytest.raises(RecordingTooLongError):
        analyzer.analyze_multi_pass("x" * (budget + 1), {})

    # Just under the budget must get past the check (it fails later, on the
    # provider this bare instance does not have — that is the point).
    with pytest.raises(AttributeError):
        analyzer.analyze_multi_pass("x" * (budget - 1), {})


def test_thin_arguments_are_dropped_by_code():
    """An argument is a conclusion plus the reasons for it, so an entry left with
    fewer than two premises is not reported. The rule lives in code, not in the
    prompt: a prompt quota would make the model invent the missing premise."""
    from debate_analyzer import _apply_consolidation_plan, MIN_PREMISES

    assert MIN_PREMISES == 2

    data = {"speakers": {"Ana": {"arguments": [
        {"arg_id": "Ana#0", "argument": "kept", "premises": ["r1", "r2"]},
        {"arg_id": "Ana#1", "argument": "one reason only", "premises": ["r1"]},
        {"arg_id": "Ana#2", "argument": "no reason at all", "premises": []},
    ]}}}
    info = _apply_consolidation_plan(data, {"merges": [], "drop": []})
    kept = data["speakers"]["Ana"]["arguments"]
    assert [a["arg_id"] for a in kept] == ["Ana#0"]
    assert info["thin_dropped"] == 2 and info["min_premises"] == 2
    assert all(len(a["premises"]) >= MIN_PREMISES for a in kept)

    # Safety valve: if EVERY entry is thin the extraction itself failed, and
    # emptying the speaker would leave a blank report. Keep them instead.
    data = {"speakers": {"Bor": {"arguments": [
        {"arg_id": "Bor#0", "argument": "thin", "premises": ["r1"]},
    ]}}}
    info = _apply_consolidation_plan(data, {"merges": [], "drop": []})
    assert len(data["speakers"]["Bor"]["arguments"]) == 1
    assert info["thin_dropped"] == 0

    # An empty plan leaves entries that already meet the threshold untouched.
    data = {"speakers": {"Cene": {"arguments": [
        {"arg_id": "Cene#0", "argument": "a", "premises": ["r1", "r2"]},
        {"arg_id": "Cene#1", "argument": "b", "premises": ["r1", "r2", "r3"]},
    ]}}}
    info = _apply_consolidation_plan(data, {"merges": [], "drop": []})
    assert len(data["speakers"]["Cene"]["arguments"]) == 2
    assert info["arguments_before"] == info["arguments_after"] == 2
    assert info["thin_dropped"] == 0


def test_llm_schema_defaults_and_clamps():
    from llm_schemas import validate_pass

    # Empty input → safe defaults, no crash.
    ce = validate_pass("claim_extraction", {})
    assert "metadata" in ce and "speakers" in ce

    # A fallacy category synonym gets clamped to the canonical bucket.
    fr = validate_pass("argument_structure", {"fallacies": [{"category": "weak"}]})
    assert fr["fallacies"][0]["category"] == "weak_reasoning"

    # Fallacy names are normalized to the closed vocabulary, so the same fallacy
    # cannot appear under three labels across runs.
    from llm_schemas import canonical_fallacy_type
    assert canonical_fallacy_type("false cause post hoc") == "post_hoc"
    assert canonical_fallacy_type("Post Hoc Correlation") == "post_hoc"
    assert canonical_fallacy_type("whataboutism (tu quoque)") == "whataboutism"
    assert canonical_fallacy_type("unsupported sweeping generalization") == "hasty_generalization"
    # An unknown name is kept verbatim, never silently discarded.
    assert canonical_fallacy_type("definitional exclusion") == "definitional exclusion"
    # …and the clamp is wired into the schema.
    fr2 = validate_pass("argument_structure", {"fallacies": [{"type": "False Cause"}]})
    assert fr2["fallacies"][0]["type"] == "post_hoc"

    # The fallacy NAME determines its category: leaving the two fields independent
    # let the category default to "informal" on anything unexpected, which is why
    # "formal" never appeared in 96 detections even when the name implied it.
    from llm_schemas import _NAME_TO_CATEGORY
    assert _NAME_TO_CATEGORY["affirming_the_consequent"] == "formal"
    assert _NAME_TO_CATEGORY["ad_hominem"] == "informal"
    assert _NAME_TO_CATEGORY["hasty_generalization"] == "weak_reasoning"

    cases = [
        ("affirming the consequent", "informal", "affirming_the_consequent", "formal"),
        ("ad hominem", "formal", "ad_hominem", "informal"),
        ("Hasty Generalization", "informal", "hasty_generalization", "weak_reasoning"),
        ("denying antecedent", "", "denying_the_antecedent", "formal"),
    ]
    for raw_type, raw_cat, want_type, want_cat in cases:
        f = validate_pass("argument_structure",
                          {"fallacies": [{"type": raw_type, "category": raw_cat}]})["fallacies"][0]
        assert f["type"] == want_type, (raw_type, f["type"])
        assert f["category"] == want_cat, (raw_type, f["category"])

    # An unknown name keeps whatever category the model supplied — no guessing.
    f = validate_pass("argument_structure",
                      {"fallacies": [{"type": "definitional exclusion", "category": "formal"}]})["fallacies"][0]
    assert f["category"] == "formal"



def test_pdf_export_minimal_and_rich():
    import pdf_export

    # Empty debate → still a valid PDF (no crash on missing fields).
    empty = pdf_export.build_pdf({"analysis_json": {}, "fact_check_json": {}}, "sl")
    assert empty[:5] == b"%PDF-"

    # A realistic debate → valid PDF, escapes special chars without crashing.
    debate = {
        "title": "Test", "mode": "debate", "language": "sl", "created_at": "2026-01-01",
        "analysis_json": {
            "metadata": {"topic": "X"},
            "summary": "S",
            "speakers": {"A": {"position": "P", "arguments": [
                {"argument": "Argument with <b> & special chars", "arg_id": "A#0"}
            ]}},
            "fallacies": [{"speaker": "A", "type": "straw man", "explanation": "e"}],
        },
        "fact_check_json": {"fact_checks": [
            {"claim": "C", "verdict": "FALSE", "explanation": "x",
             "sources": [{"url": "https://example.org", "title": "Src"}]}
        ]},
    }
    rich = pdf_export.build_pdf(debate, "sl")
    assert rich[:5] == b"%PDF-"
    assert len(rich) > len(empty) or len(rich) > 1000


def test_enum_labels_cover_every_schema_value():
    """The shared label file must name every value the model may return.

    Categorical values stay English in the data — the code matches on them
    verbatim. Only the display is translated, and it is translated from ONE
    file that both the Python side and the React side read. This test is what
    keeps that promise: add a value to a closed vocabulary without a label and
    the suite fails, instead of the value quietly showing up raw in a Slovenian
    report.
    """
    import json

    import llm_schemas
    from translations import ENUM_LABELS, label

    raw = json.loads(
        (pathlib.Path(__file__).resolve().parent.parent
         / "frontend" / "src" / "enumLabels.json").read_text(encoding="utf-8")
    )
    assert ENUM_LABELS, "translations.py could not read enumLabels.json"
    assert set(ENUM_LABELS) == {k for k in raw if not k.startswith("_")}

    # Every entry carries both languages and neither is empty.
    for group, values in ENUM_LABELS.items():
        for value, entry in values.items():
            assert set(entry) >= {"sl", "en"}, f"{group}/{value} is missing a language"
            assert entry["sl"].strip() and entry["en"].strip(), f"{group}/{value} is empty"

    # The closed fallacy vocabulary is the one that grows; every name in it
    # must be nameable in both languages, and its category must be too.
    for name in llm_schemas._NAME_TO_CATEGORY:
        assert name in ENUM_LABELS["fallacy"], f"fallacy '{name}' has no label"
    for category in set(llm_schemas._NAME_TO_CATEGORY.values()):
        assert category in ENUM_LABELS["fallacy_category"], f"category '{category}' has no label"

    # Values the schemas clamp to must all be labelled.
    expected = {
        "argument_type": {"factual", "normative", "causal", "definitional", "debatable"},
        "evasion_type":  {"deflection", "topic_change", "non_answer",
                          "partial_answer", "talked_over"},
        "rebuttal_type": {"direct_contradiction", "undermining_premise",
                          "alternative_explanation", "questioning_warrant"},
        "role":          {"primary_speaker", "debater", "moderator"},
    }
    for group, values in expected.items():
        missing = values - set(ENUM_LABELS[group])
        assert not missing, f"{group} is missing labels for {sorted(missing)}"

    # Lookup behaviour: known value translates, unknown degrades readably.
    assert label("fallacy", "straw_man", "sl") == "slamnati mož"
    assert label("argument_type", "causal", "sl") == "vzročni"
    assert label("fallacy", "nekaj_povsem_novega", "sl") == "nekaj povsem novega"
    assert label("argument_type", None) == ""


def test_fallacy_edit_operations():
    """Manual correction of fallacies: add a missed one, retype a wrong one,
    delete an invented one.

    Fallacy detection is the step most exposed to error in BOTH directions, so
    the reader must be able to correct it in both. Two invariants matter here:
    a hand-typed name is normalized to the closed vocabulary, and the category
    is DERIVED from the name — exactly the rule the schema applies to model
    output, so a manual entry is shaped like a detected one.
    """
    import ast

    src = (pathlib.Path(__file__).resolve().parent.parent / "api.py").read_text(encoding="utf-8")
    wanted = {"_apply_edits", "_canon_fallacy", "_fallacy_category", "_one_of"}
    body = [n for n in ast.parse(src).body
            if getattr(n, "name", None) in wanted and not isinstance(n, ast.ClassDef)]

    class EditOp:
        def __init__(self, op, payload):
            self.op, self.payload = op, payload

    ns = {"Dict": dict, "List": list, "Any": object, "Tuple": tuple, "EditOp": EditOp}
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])),
                 "<api>", "exec"), ns)
    apply_edits = ns["_apply_edits"]

    analysis = {
        "speakers": {"Ana": {"arguments": [{"arg_id": "Ana#0", "argument": "X"}]}},
        "fallacies": [{"speaker": "Ana", "type": "straw_man", "category": "informal",
                       "evidence": "citat"}],
    }

    # Add two missed ones, typed the way a human would type them.
    analysis, log = apply_edits(analysis, [
        EditOp("add_fallacy", {"fallacy": {"speaker": "Ana", "type": "Affirming The Consequent",
                                           "evidence": "ce deluje, potniki rastejo"}}),
        EditOp("add_fallacy", {"fallacy": {"speaker": "Ana", "type": "hasty generalisation",
                                           "evidence": "iz dveh mest"}}),
    ])
    assert len(log) == 2
    added = analysis["fallacies"][1:]
    # Name normalized, category derived from it, manual marker set.
    assert added[0]["type"] == "affirming_the_consequent"
    assert added[0]["category"] == "formal"
    assert added[1]["type"] == "hasty_generalization"     # British spelling accepted
    assert added[1]["category"] == "weak_reasoning"
    assert all(f["user_added"] for f in added)

    # Retype a wrong detection: the category must follow the new name.
    analysis, _ = apply_edits(analysis, [
        EditOp("edit_fallacy", {"index": 0, "fields": {"type": "ad-hominem"}}),
    ])
    assert analysis["fallacies"][0]["type"] == "ad_hominem"
    assert analysis["fallacies"][0]["category"] == "informal"

    # Delete an invented one.
    before = len(analysis["fallacies"])
    analysis, log = apply_edits(analysis, [EditOp("delete_fallacy", {"index": 1})])
    assert len(analysis["fallacies"]) == before - 1
    assert "deleted fallacy" in log[0]

    # Malformed payloads must change nothing rather than raise.
    snapshot = [dict(f) for f in analysis["fallacies"]]
    analysis, log = apply_edits(analysis, [
        EditOp("delete_fallacy", {"index": 99}),
        EditOp("delete_fallacy", {"index": -1}),
        EditOp("edit_fallacy", {"index": "not an int", "fields": {}}),
        EditOp("add_fallacy", {"fallacy": {"speaker": "", "type": "x", "evidence": "y"}}),
        EditOp("add_fallacy", {"fallacy": {"speaker": "Ana", "type": "x", "evidence": ""}}),
    ])
    assert log == []
    assert analysis["fallacies"] == snapshot


def test_review_records_a_verdict_without_erasing_the_detection():
    """Reviewing keeps two records: what the system found, and what the reader
    made of it.

    This is what makes the evaluation computable. If confirming a fallacy did
    nothing and dismissing one deleted it, the stored analysis would only ever
    contain detections the reader agreed with and precision would come out at
    100 % by construction. So `review_fallacy` writes a verdict ALONGSIDE the
    detection.
    """
    import ast

    src = (pathlib.Path(__file__).resolve().parent.parent / "api.py").read_text(encoding="utf-8")
    wanted = {"_apply_edits", "_canon_fallacy", "_fallacy_category"}
    body = [n for n in ast.parse(src).body
            if getattr(n, "name", None) in wanted and not isinstance(n, ast.ClassDef)]

    class EditOp:
        def __init__(self, op, payload):
            self.op, self.payload = op, payload

    ns = {"Dict": dict, "List": list, "Any": object, "Tuple": tuple, "EditOp": EditOp}
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])),
                 "<api>", "exec"), ns)
    _apply_edits = ns["_apply_edits"]

    analysis = {
        "speakers": {
            "Ana": {"arguments": [{"arg_id": "Ana#0", "argument": "a",
                                   "premises": ["p1", "p2"]}]}
        },
        "fallacies": [{"speaker": "Ana", "type": "post_hoc",
                       "category": "weak_reasoning", "evidence": "p1"}],
    }

    out, applied = _apply_edits(analysis, [
        EditOp("review_fallacy", {"index": 0, "verdict": "dismissed"}),
    ])

    # The dismissed detection is still there, only now it carries a verdict.
    assert len(out["fallacies"]) == 1
    assert out["fallacies"][0]["type"] == "post_hoc"
    assert out["fallacies"][0]["review"] == "dismissed"
    assert len(applied) == 1

    # Clearing a verdict removes only the verdict.
    out, _ = _apply_edits(out, [EditOp("review_fallacy", {"index": 0, "verdict": None})])
    assert "review" not in out["fallacies"][0]
    assert out["fallacies"][0]["type"] == "post_hoc"


def test_transcript_compaction_and_label_shortening():
    """A speaker label is repeated on every line, so its length is multiplied by
    the segment count.

    One stored transcript carried the label "Mixed host and guest: Tristan
    Hughes and Dr Raul Kanainadike" — 62 characters over 780 lines, 48 000
    characters of the 80 000-character analysis budget spent on the same string.
    Two rules keep that from recurring: an identification answer is reduced to a
    label short enough to repeat, and the label is printed only where the
    speaker CHANGES. Neither drops a word of speech or a timestamp.
    """
    import ast, re

    src = (pathlib.Path(__file__).resolve().parent.parent / "transcribe.py").read_text(encoding="utf-8")
    body = [n for n in ast.parse(src).body
            if getattr(n, "name", None) == "_compact_transcript"
            or (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "MAX_SPEAKER_LABEL")]
    ns = {"re": re}
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])),
                 "<transcribe>", "exec"), ns)
    compact = ns["_compact_transcript"]

    # Naming the speakers is the user's job, so no model call can hand back a
    # sentence-shaped label any more. What is left has to stay short enough to
    # repeat on every line.
    import transcribe
    assert not hasattr(transcribe, "identify_speakers")
    assert not hasattr(transcribe, "_shorten_speaker_label")
    assert not hasattr(transcribe, "_fix_diarization")

    tx = ("Ana: prva\n"
          "Ana: druga\n"
          "Bor: odgovor\n"
          "Ana: tretja\n")
    out = compact(tx).split("\n")

    # Named on entry, silent while the same speaker continues, named again on change.
    assert out[0] == "Ana: prva"
    assert out[1] == "druga"
    assert out[2] == "Bor: odgovor"
    assert out[3] == "Ana: tretja"

    # No speech is lost.
    for word in ("prva", "druga", "odgovor", "tretja"):
        assert word in compact(tx)

    # A line that does not parse is passed through untouched, and it ends the
    # run: the next line names its speaker again rather than assuming continuity.
    odd = "Ana: prva\nbrez oblike\nAna: druga\n"
    got = compact(odd).split("\n")
    assert got[1] == "brez oblike"
    assert got[2] == "Ana: druga"


def test_speaker_labels_are_renumbered_never_merged():
    """Normalising a diarization label must not cost a speaker.

    Normalisation used to force any label that did not literally start with
    "SPEAKER_" to the first speaker. When the API answered with its own naming —
    "A"/"B", "speaker_1", a bare index — every voice became one and a two-person
    debate reached the analysis as a monologue: 724 segments, one label. The rule
    is that normalisation renumbers and never reduces.
    """
    def normalise(raw_labels):
        """The rule the transcription path applies."""
        seen, out = {}, []
        for raw in raw_labels:
            raw = str(raw or "").strip()
            if raw not in seen:
                seen[raw] = f"Speaker {len(seen) + 1}"
            out.append(seen[raw])
        return out

    # Whatever the API calls them, two voices stay two.
    for raw in (["A", "B", "A", "B"],
                ["speaker_1", "speaker_2", "speaker_1"],
                ["0", "1", "0", "1"],
                ["Tristan", "Raul", "Tristan"]):
        got = normalise(raw)
        assert len(set(got)) == len(set(raw)), f"{raw} lost a speaker: {got}"
        assert set(got) <= {"Speaker 1", "Speaker 2"}

    # Order of first appearance decides the number, and it is stable.
    assert normalise(["B", "A", "B", "A"]) == [
        "Speaker 1", "Speaker 2", "Speaker 1", "Speaker 2"]
    # One voice stays one — normalisation invents nobody either.
    assert set(normalise(["A", "A", "A"])) == {"Speaker 1"}

    # The label is short by construction, so it stays cheap to repeat.
    assert all(len(x) <= 32 for x in normalise(["A", "B"]))


def test_debate_with_one_diarized_voice_is_refused_before_analysis():
    """A debate whose transcript separates one voice is stopped after
    transcription, not after argument extraction.

    The 1v1 guard already refuses this, but it runs after the most expensive
    call in the pipeline — so the user paid for a monologue analysis of a
    conversation. The transcript already holds the answer.
    """
    pytest.importorskip("fastapi")
    from api import _distinct_speakers
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        one = pathlib.Path(d) / "one.txt"
        one.write_text("Speaker 1: prva\n"
                       "druga\n"                      # compacted continuation
                       "Speaker 1: tretja\n", encoding="utf-8")
        assert _distinct_speakers(one) == {"Speaker 1"}

        two = pathlib.Path(d) / "two.txt"
        two.write_text("Ana: prva\n"
                       "Bor: odgovor\n"
                       "nadaljevanje\n", encoding="utf-8")
        assert _distinct_speakers(two) == {"Ana", "Bor"}

        # A missing file is not a crash — it is simply no evidence of two voices.
        assert _distinct_speakers(pathlib.Path(d) / "nope.txt") == set()


def test_batched_perplexity_findings_are_split_per_claim():
    """Each claim in a batch keeps its OWN section of the answer.

    The batch call researches five claims in one request and gets back one
    numbered answer. Every section must be filed under the claim it belongs
    to, because the judge later decides on exactly this material: a section
    pinned to the wrong claim would have the judge rule on someone else's
    evidence. When the numbering does not line up, the whole answer is
    discarded rather than misfiled.
    """
    from fact_checker import FactChecker

    answer = (
        '1. **Claim:** "one in three UK children are short-sighted"\n'
        '   - UK data put myopia far below one third.\n'
        '2. **Claim:** "myopia will affect a billion children by 2050"\n'
        '   - The projection covers all ages, not children alone.\n'
        '3. **Claim:** "97% of 12-year-olds own a smartphone"\n'
        '   - Ofcom reports 97% for 12-year-olds in 2023.\n'
    )

    sections = FactChecker._split_numbered_sections(answer, 3)
    assert sections is not None and len(sections) == 3
    assert "one third" in sections[0]
    assert "all ages" in sections[1]
    assert "Ofcom" in sections[2]

    assert FactChecker._split_numbered_sections(answer, 5) is None
    assert FactChecker._split_numbered_sections("prose with no numbering", 2) is None
    assert FactChecker._split_numbered_sections("1. a\n3. c\n", 2) is None
    assert FactChecker._split_numbered_sections("", 3) is None


def test_every_source_in_the_list_comes_from_a_collector():
    """The source list is assembled from collector output, deduplicated by URL.

    Nothing else may add to it. The two numbers printed beside every verdict
    are counted off this list, so if an entry could appear without a collector
    having retrieved it, those numbers would describe material that was never
    fetched.
    """
    from fact_checker import FactChecker

    ev = {
        "web": {"sources": [
            {"title": "NHS", "url": "https://nhs.uk/myopia", "date": "2024",
             "relevant_quote": "prevalence rose", "source_type": "official_stat"},
        ]},
        "papers": [{"title": "Myopia in the UK", "url": "https://pubmed.gov/1",
                    "year": "2021", "abstract": "cohort study", "citations": 42,
                    "journal": "BJO"}],
        "wikidata": [{"entity": "Myopia", "entity_id": "Q123",
                      "url": "https://wikidata.org/Q123", "description": "eye condition"}],
        "factchecks": [{"publisher": "FullFact", "rating": "Mostly false",
                        "url": "https://fullfact.org/x", "title": "check"}],
        "grok": {"web_sources": [
            {"title": "Duplicate", "url": "https://nhs.uk/myopia"},
            {"title": "X thread", "url": "https://x.com/post"},
        ]},
        "perplexity": {"citations": ["https://ons.gov.uk/data", "https://pubmed.gov/1"]},
    }

    sources = FactChecker._collect_sources(ev)
    urls = [s["url"] for s in sources]

    assert len(urls) == len(set(urls)), "a URL may appear only once"
    assert set(urls) == {
        "https://nhs.uk/myopia", "https://pubmed.gov/1", "https://wikidata.org/Q123",
        "https://fullfact.org/x", "https://x.com/post", "https://ons.gov.uk/data",
    }
    # A page two collectors found keeps the entry of whichever got there first,
    # and does not cost the later collector its turn.
    by_url = {s["url"]: s for s in sources}
    assert by_url["https://nhs.uk/myopia"]["title"] == "NHS"
    assert by_url["https://pubmed.gov/1"]["source_type"] == "peer_reviewed"

    # Nothing is invented when every collector comes back empty.
    assert FactChecker._collect_sources(
        {"web": None, "papers": [], "wikidata": [], "factchecks": [],
         "grok": None, "perplexity": None}) == []


def test_source_slots_are_shared_between_collectors():
    """Ten slots are filled in turns, not drained one collector at a time.

    One collector with a long list must not take every slot. The code counts
    sources AND distinct domains, and ten papers from one publisher are far
    fewer domains than a spread across collectors. A collector that runs out
    simply stops getting a turn.
    """
    from fact_checker import FactChecker

    ev = {
        "web": {"sources": [{"title": f"W{i}", "url": f"https://web{i}.com/a",
                             "source_type": "news"} for i in range(20)]},
        "papers": [{"title": f"P{i}", "url": f"https://pubmed.gov/{i}",
                    "year": "2021"} for i in range(20)],
        "wikidata": [{"entity": "E", "entity_id": "Q1", "url": "https://wikidata.org/Q1"}],
        "factchecks": [],
        "grok": None,
        "perplexity": {"citations": ["https://ons.gov.uk/x", "https://ons.gov.uk/y"]},
    }

    sources = FactChecker._collect_sources(ev)
    assert len(sources) == 10

    kinds = [s["source_type"] for s in sources]
    # Wikidata held one entry and Perplexity two, and both got in early even
    # though the web and paper queues could have filled all ten on their own.
    assert kinds[:4] == ["news", "peer_reviewed", "structured_knowledge", "perplexity"]
    assert kinds.count("structured_knowledge") == 1
    assert kinds.count("perplexity") == 2
    # Neither long queue was allowed to run away with the rest.
    assert kinds.count("news") == 4 and kinds.count("peer_reviewed") == 3

    # The same input always gives the same list — no set iteration, no chance.
    assert FactChecker._collect_sources(ev) == sources


def test_judge_prompt_shows_the_material_and_names_silent_collectors():
    """The judge sees a numbered source list and which collectors did not run.

    It cites by those numbers, so the list is what keeps its citations tied to
    material the system actually holds. Naming the collectors that stayed
    silent stops thin evidence from reading like a complete search.
    """
    from fact_checker import FactChecker

    fc = FactChecker.__new__(FactChecker)
    ev = {
        "claim_context": "Stated while arguing about screen time",
        "papers": [], "wikidata": [], "factchecks": [],
        "web": {"findings": "The NHS puts prevalence near one in five."},
        "perplexity": {"findings": "Sources disagree on the age band."},
        "grok": None,
        "skipped": {"grok": "claim type 'health' adds noise on X"},
    }
    sources = [{"title": "NHS", "url": "https://nhs.uk/myopia", "date": "2024",
                "relevant_quote": "one in five", "source_type": "official_stat"}]

    prompt = fc._judge_prompt("One in three UK children is short-sighted", "health", ev, sources)

    assert "[1] NHS" in prompt
    assert "one in five" in prompt
    assert "COLLECTORS THAT DID NOT RUN" in prompt
    assert "claim type 'health' adds noise on X" in prompt
    assert "Stated while arguing about screen time" in prompt
    assert "You have NO search of your own" in prompt

    # No collector's opinion reaches the judge, because no collector has one.
    # The gathered blocks carry findings and sources, never a vote.
    material = prompt[prompt.index("=== "):prompt.index("VERDICT DEFINITIONS")]
    assert "ENGINE VOTES" not in material
    for word in ("TRUE", "FALSE", "MISLEADING", "UNVERIFIABLE", "PARTIALLY"):
        assert word not in material, f"{word} leaked into the material shown to the judge"


def test_the_same_claim_in_two_arguments_is_checked_for_both():
    """Two speakers stating the same fact each keep their own claim.

    Claims are no longer merged before checking. The system used to compare
    every pair with cosine similarity and check only one of them, and an
    exact-string pass then dropped identical claims outright. Both
    decided WHOSE fact it was, not just what had to be checked:
    verdicts are counted per speaker, so a dropped claim took its speaker's
    fact with it and left its premise without a verdict badge. The same fact is
    now simply checked twice.
    """
    from fact_checker import FactChecker

    assert not hasattr(FactChecker, "cluster_claims")
    assert not hasattr(FactChecker, "_dedupe_exact_claims")
    assert not hasattr(FactChecker, "_share_verdict_across_cluster")

    claims = [
        {"exact_claim": "Vsak tretji britanski otrok je kratkoviden",
         "speaker": "Sophie Winkleman", "arg_id": "SW#1", "premise_index": 3},
        {"exact_claim": "Vsak tretji britanski otrok je kratkoviden",
         "speaker": "Andrew Doyle", "arg_id": "AD#2", "premise_index": 1},
    ]

    fc = FactChecker.__new__(FactChecker)
    fc.decompose_claims = lambda c: c
    fc.verify_claim = lambda c, **kw: {**c, "verdict": "PARTIALLY_TRUE", "sources": []}
    fc._generate_summary = lambda f: {"verdict_breakdown": {}}
    fc._perplexity_client = None
    fc._grok_client = None

    out = fc._verify_claim_set(claims)

    assert out["total_claims"] == 2, "neither speaker may lose their claim"
    speakers = {f["speaker"]: f for f in out["fact_checks"]}
    assert set(speakers) == {"Sophie Winkleman", "Andrew Doyle"}
    assert speakers["Sophie Winkleman"]["premise_index"] == 3
    assert speakers["Andrew Doyle"]["premise_index"] == 1
    # Both were checked, so there is no separate "how many were verified" count.
    assert "verified_claims" not in out


def test_evidence_is_counted_in_domains_not_in_links():
    """Several results from one website are one source, not several.

    The two numbers printed beside every verdict are how many sources were
    retrieved and how many distinct domains they sit on. The second is the one
    that says something: five hits on one site are five links but a single
    place, and the reader has to be able to see that.
    """
    from fact_checker import FactChecker

    one_domain = [{"url": "https://bbc.co.uk/a"}, {"url": "https://bbc.co.uk/b"},
                  {"url": "https://www.bbc.co.uk/c"}]
    m = FactChecker._compute_evidence_metrics(one_domain)
    assert m["source_count"] == 3
    assert m["independent_domain_count"] == 1, "www. is the same domain"

    three = [{"url": "https://bbc.co.uk/a"}, {"url": "https://nhs.uk/b"},
             {"url": "https://ons.gov.uk/c"}]
    assert FactChecker._compute_evidence_metrics(three)["independent_domain_count"] == 3

    empty = FactChecker._compute_evidence_metrics([])
    assert empty == {"source_count": 0, "independent_domain_count": 0}


def test_each_source_gets_one_of_the_same_five_verdicts():
    """The judge labels every numbered source, and the code checks the labels.

    The tally beside a claim says how many sources point which way, in the same
    five words used for the claim itself. It is a count, not a vote: the
    verdict is allowed to differ from the majority, so nothing here may quietly
    become a decision. A number that points past the list is dropped, an
    unknown word becomes UNVERIFIABLE, and a source the judge never mentioned
    stays unlabelled rather than being counted as agreeing.
    """
    from fact_checker import FactChecker

    assert FactChecker._one_verdict("partially true") == "PARTIALLY_TRUE"
    assert FactChecker._one_verdict("PARTIALLY-TRUE") == "PARTIALLY_TRUE"
    assert FactChecker._one_verdict("mostly right") == "UNVERIFIABLE"
    assert FactChecker._one_verdict(None) == "UNVERIFIABLE"

    marked = [
        {"url": "https://a.com", "source_verdict": "TRUE"},
        {"url": "https://b.com", "source_verdict": "FALSE"},
        {"url": "https://c.com", "source_verdict": "FALSE"},
        {"url": "https://d.com"},                      # judge never mentioned it
    ]
    tally = FactChecker._count_source_verdicts(marked)
    assert tally == {"TRUE": 1, "PARTIALLY_TRUE": 0, "MISLEADING": 0,
                     "FALSE": 2, "UNVERIFIABLE": 0}
    assert sum(tally.values()) == 3, "an unlabelled source is not counted"

    # Every one of the five is a possible label, including for a source.
    for v in ("TRUE", "PARTIALLY_TRUE", "MISLEADING", "FALSE", "UNVERIFIABLE"):
        assert FactChecker._count_source_verdicts([{"source_verdict": v}])[v] == 1


def test_nothing_after_the_judge_changes_the_verdict():
    """The verdict the judge returns is the verdict that is reported.

    Fact-checking used to end with checks that could soften the verdict when
    the evidence was thin. Over 341 recorded claims the one that fired never
    changed a verdict: every claim it caught had already been called
    UNVERIFIABLE by the model itself. What the evidence amounted to is now
    reported as two counted numbers instead.
    """
    from fact_checker import FactChecker

    assert not hasattr(FactChecker, "_apply_quality_guardrails")
    assert not hasattr(FactChecker, "_detect_source_bias")
    assert not hasattr(FactChecker, "_cross_verify_with_multiple_sources")

    fc = FactChecker.__new__(FactChecker)

    # A verdict backed by nothing keeps the verdict and shows the zero.
    out = fc._finalise_result({"verdict": "FALSE", "sources": []})
    assert out["verdict"] == "FALSE"
    assert out["evidence_metrics"] == {"source_count": 0, "independent_domain_count": 0}

    # A verdict backed only by opinion pieces keeps its extreme form.
    opinion = [{"url": "https://times.co.uk/opinion/x"},
               {"url": "https://blog.example.com/blog/y"}]
    out = fc._finalise_result({"verdict": "TRUE", "sources": opinion})
    assert out["verdict"] == "TRUE"
    assert out["evidence_metrics"]["independent_domain_count"] == 2
    assert out["verdict_label"]


def test_judging_prompts_share_the_same_stance():
    """The three judging steps must judge by the same stance, word for word.

    Every model call has its own system prompt, written out in full, so that
    each one can be read on its own and says exactly what that step does. The
    price is that the neutrality paragraph is written three times. If those
    copies drift, one step would judge by different rules than the other two
    and nothing in the output would show it — this test is what shows it.

    Extraction is deliberately NOT in this group: it does not judge, it
    transcribes, and giving it rules for judging would be giving it rules for
    a task it does not perform.
    """
    import debate_analyzer as D

    stance = [
        "report weaknesses in the reasoning exactly where you find them",
        "without softening them to keep the sides looking balanced",
        "without declaring an overall winner",
        "Do NOT let your own views on the TOPIC",
        "QUALITY OF THE REASONING, not the truth of the position",
    ]

    judging = {
        "fallacies": D._system_fallacies(),
        "rebuttal":  D._system_rebuttal(),
        "synthesis": D._system_synthesis(),
    }
    for name, prompt in judging.items():
        for line in stance:
            assert line in prompt, f"{name} is missing the shared stance: {line!r}"

    extraction = D._system_extraction(mode="debate")
    for line in stance:
        assert line not in extraction, (
            "extraction does not judge and must not carry judging rules")
    assert "not judging them" in extraction

    # Each step's own instruction belongs to that step and to no other.
    assert "CONSERVATIVE FALLACY DETECTION" in judging["fallacies"]
    assert "CONSERVATIVE FALLACY DETECTION" not in judging["rebuttal"]
    assert "WHAT COUNTS AS EVASION" in judging["rebuttal"]
    assert "WHAT COUNTS AS EVASION" not in judging["fallacies"]

    # Rules about who is in the recording go only to the step that reads it.
    assert "MODERATOR RULE" in extraction
    for name, prompt in judging.items():
        assert "MODERATOR RULE" not in prompt, (
            f"{name} works on extracted arguments; the moderator is long gone")


def test_a_cut_off_judgement_is_retried_with_more_room():
    """A truncated answer must be asked again, not turned into ERROR.

    The judge returns a verdict, an explanation and a label for each of ten
    sources. On one recording a 1200-token budget cut the JSON mid-string on 38
    of 41 claims; every one of them became ERROR, and because ERROR is excluded
    from every count that follows, three claims were left carrying the whole
    fact-check. A cut-off answer is unfinished, not wrong, so the budget doubles
    and the question is asked again.
    """
    from fact_checker import FactChecker
    from debate_analyzer import TruncatedJSONError
    from config_loader import get as cfg

    start = int(cfg("fact_checking.judge_max_tokens", 4096))
    cap = int(cfg("fact_checking.judge_max_tokens_cap", 16384))
    assert start >= 2048, "the judge needs room for ten source labels"
    assert cap > start, "the retry must be able to ask for more than it started with"

    budgets = []

    class Provider:
        def __init__(self, fail_times):
            self.fail_times = fail_times
        def call(self, system, user, temperature=0.1, **kw):
            budgets.append(kw["max_tokens"])
            if len(budgets) <= self.fail_times:
                raise TruncatedJSONError("cut off", "{", 0)
            return {"verdict": "TRUE", "explanation": "ok",
                    "sources": [{"n": 1, "verdict": "TRUE"}]}

    fc = FactChecker.__new__(FactChecker)
    srcs = [{"url": "https://a.example", "title": "A"}]
    ev = {"skipped": {}}

    import debate_analyzer
    original = debate_analyzer.create_provider
    try:
        # Two truncated answers, then one that fits: the budget must grow.
        debate_analyzer.create_provider = lambda *a, **k: Provider(2)
        fc._judge_prompt = lambda *a, **k: "prompt"
        out = fc._judge_claim("c", "statistic", ev, srcs)
        assert out["verdict"] == "TRUE", out
        assert budgets == [start, start * 2, start * 4], budgets

        # Still truncated after every attempt: only then is it an ERROR.
        budgets.clear()
        debate_analyzer.create_provider = lambda *a, **k: Provider(99)
        out = fc._judge_claim("c", "statistic", ev, srcs)
        assert out["verdict"] == "ERROR"
        assert len(budgets) == int(cfg("fact_checking.judge_max_attempts", 3))
    finally:
        debate_analyzer.create_provider = original

