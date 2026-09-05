"""
Centralized configuration loader.
Reads config.yaml and provides typed access with defaults.

Concurrent-safe overrides:
  Background jobs run in their own thread and need to override pipeline.mode,
  pipeline.language, pipeline.data_dir, pipeline.output_dir, etc. WITHOUT
  trampling on other concurrent jobs. Each thread gets its own override stack
  via threading.local(). `get()` checks the current thread's overrides first,
  then falls back to the shared base config.

  Usage in pipeline runner:
      from config_loader import job_overrides
      with job_overrides(**{"pipeline.mode": "debate", "pipeline.data_dir": "..."}):
          # all cfg() reads in this thread see the overrides
          run_pipeline()
"""

import contextlib
import threading
from pathlib import Path
from typing import Any, Iterator

import yaml


_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"
_config: dict | None = None
_load_lock = threading.Lock()

# Per-thread override stack. Each thread sees only its own overrides.
# Stored as {dotpath: value}.
_local = threading.local()


def load_config(path: Path | str | None = None) -> dict:
    global _config
    if _config is not None and path is None:
        return _config

    with _load_lock:
        if _config is not None and path is None:
            return _config
        path = Path(path) if path else _DEFAULT_CONFIG_PATH
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        with open(path, encoding="utf-8") as f:
            _config = yaml.safe_load(f)
        return _config


def get(dotpath: str, default: Any = None) -> Any:
    """Get nested config value with dot notation: get('fact_checking.parallel_workers').

    Thread-local overrides (set via job_overrides) take precedence over the
    shared base config. This lets concurrent pipeline runs use different
    pipeline.mode / data_dir / output_dir without race conditions.
    """
    overrides = getattr(_local, "overrides", None)
    if overrides and dotpath in overrides:
        val = overrides[dotpath]
        return default if val is None else val

    cfg = load_config()
    keys = dotpath.split(".")
    val: Any = cfg
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
        else:
            return default
        if val is None:
            return default
    return val


# ── Združljivost novejših modelov ───────────────────────────────────────────
# Generacije modelov sprejemajo različne parametre: GPT-5 in o-serija ne
# sprejmeta temperature in zahtevata max_completion_tokens, Claude 5 je
# temperature upokojil. Ta pomočnika to skrijeta pred klicnimi mesti.

_OPENAI_RESTRICTED_PREFIXES = ("gpt-5", "o1", "o3", "o4")
_ANTHROPIC_NO_TEMP_PREFIXES = ("claude-sonnet-5", "claude-opus-5",
                               "claude-haiku-5", "claude-fable-5")


def model_supports_temperature(model: str) -> bool:
    """False za modele, ki temperature ne sprejmejo (GPT-5, o-serija, Claude 5)."""
    m = (model or "").lower()
    if m.startswith("openai/"):        # nekateri posredniki dodajo predpono
        m = m[len("openai/"):]
    if m.startswith(_OPENAI_RESTRICTED_PREFIXES):
        return False
    return not m.startswith(_ANTHROPIC_NO_TEMP_PREFIXES)


def sampling_kwargs(model: str, temperature: float | None = None,
                    max_tokens: int | None = None) -> dict:
    """Parametri vzorčenja, prilagojeni generaciji modela.

    Uporaba na klicnem mestu:
        client.chat.completions.create(
            model=m, messages=[...], **sampling_kwargs(m, 0.0),
            response_format={"type": "json_object"})
    """
    kwargs: dict[str, Any] = {}
    if temperature is not None and model_supports_temperature(model):
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        m = (model or "").lower()
        # max_completion_tokens zahteva samo nova OpenAI generacija;
        # Anthropic in vsi ostali ostanejo pri max_tokens.
        needs_new_key = m.startswith(_OPENAI_RESTRICTED_PREFIXES)
        kwargs["max_completion_tokens" if needs_new_key else "max_tokens"] = max_tokens
    return kwargs


@contextlib.contextmanager
def job_overrides(**overrides: Any) -> Iterator[None]:
    """Context manager: apply per-job config overrides for the calling thread.

    Keys use dot notation: `job_overrides(**{"pipeline.mode": "solo"})`.
    On exit, restores the previous overrides for this thread.
    """
    prev = getattr(_local, "overrides", {}) or {}
    new = dict(prev)
    new.update(overrides)
    _local.overrides = new
    try:
        yield
    finally:
        _local.overrides = prev
