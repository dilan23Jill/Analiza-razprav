"""Nalaganje pozivov iz mape prompts/."""

from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).resolve().parent / "prompts"


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    """Vrne besedilo poziva prompts/<name>."""
    return (_DIR / name).read_text(encoding="utf-8").replace("\r\n", "\n")
