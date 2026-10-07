"""Beleženje porabe žetonov pri klicih modelov.

Vsak klic modela po odgovoru pokliče record(response). Ob koncu opravila
api.py zapiše povzetek v usage/<oznaka opravila>.json. Beleženje je skupno
za cel proces, zato je štetje točno, kadar hkrati teče le eno opravilo.

Modul je bil uporabljen za meritve porabe v diplomski nalogi. Priklop na
klice modelov (record, step) v trenutni kodi cevovoda ni vključen.
"""
import json
import sys
import threading
import time
from pathlib import Path

_lock = threading.Lock()
_calls = []
_steps = []          # [{"name", "start"}] v vrstnem redu
_current = {"step": ""}
_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens",
         "cache_write_tokens", "cached_tokens", "audio_tokens")


def reset() -> None:
    with _lock:
        _calls.clear()
        _steps.clear()
        _current["step"] = ""


def set_step(name: str) -> None:
    """Označi začetek stopnje cevovoda. Klici do naslednje oznake štejejo k njej."""
    with _lock:
        _steps.append({"name": name, "start": time.time()})
        _current["step"] = name


def _get(obj, *names):
    for name in names:
        value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
        if value is not None:
            return value
    return None


def _caller() -> str:
    names = []
    frame = sys._getframe(2)
    while frame is not None and len(names) < 3:
        names.append(frame.f_code.co_name)
        frame = frame.f_back
    return " < ".join(names)


def record(response, model: str = "") -> None:
    """Zapiše porabo enega klica. Napaka pri beleženju nikoli ne ustavi analize."""
    try:
        usage = getattr(response, "usage", None)
        entry = {
            "time": time.time(),
            "caller": _caller(),
            "step": _current["step"],
            "model": model or getattr(response, "model", None) or "?",
        }
        if usage is not None:
            entry["input_tokens"] = int(_get(usage, "input_tokens", "prompt_tokens") or 0)
            entry["output_tokens"] = int(_get(usage, "output_tokens", "completion_tokens") or 0)
            entry["cache_read_tokens"] = int(_get(usage, "cache_read_input_tokens") or 0)
            entry["cache_write_tokens"] = int(_get(usage, "cache_creation_input_tokens") or 0)
            details = _get(usage, "prompt_tokens_details", "input_tokens_details",
                           "input_token_details")
            if details is not None:
                entry["cached_tokens"] = int(_get(details, "cached_tokens") or 0)
                entry["audio_tokens"] = int(_get(details, "audio_tokens") or 0)
            seconds = _get(usage, "seconds")
            if seconds:
                entry["seconds"] = float(seconds)
        with _lock:
            _calls.append(entry)
    except Exception:
        pass


def summary() -> dict:
    with _lock:
        calls = list(_calls)
    by_model = {}
    for call in calls:
        row = by_model.setdefault(call["model"], {"calls": 0, **{k: 0 for k in _KEYS}})
        row["calls"] += 1
        for k in _KEYS:
            row[k] += int(call.get(k) or 0)
    total = {"calls": len(calls), **{k: sum(r[k] for r in by_model.values()) for k in _KEYS}}

    with _lock:
        steps = list(_steps)
    by_step = []
    for i, s in enumerate(steps):
        end = steps[i + 1]["start"] if i + 1 < len(steps) else time.time()
        row = {"step": s["name"], "seconds": round(end - s["start"], 1), "calls": 0,
               **{k: 0 for k in _KEYS}}
        for call in calls:
            if call.get("step") == s["name"]:
                row["calls"] += 1
                for k in _KEYS:
                    row[k] += int(call.get(k) or 0)
        by_step.append(row)
    return {"total": total, "by_step": by_step, "by_model": by_model, "calls": calls}


def save(job_id: str) -> None:
    try:
        folder = Path("usage")
        folder.mkdir(exist_ok=True)
        (folder / f"{job_id}.json").write_text(
            json.dumps(summary(), ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
