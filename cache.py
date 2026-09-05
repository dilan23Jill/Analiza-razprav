"""
Simple disk cache for API responses.
Avoids paying for redundant API calls during development / reruns.
"""

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Optional

from config_loader import get as cfg


class DiskCache:
    """Key-value cache stored as JSON files on disk."""

    def __init__(self, directory: str | None = None, ttl_hours: float | None = None):
        self.directory = Path(
            directory or cfg("fact_checking.cache.directory", ".cache/fact_check")
        )
        self.ttl_seconds = (
            ttl_hours or cfg("fact_checking.cache.ttl_hours", 168)
        ) * 3600
        self.enabled = cfg("fact_checking.cache.enabled", True)
        self.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _key_hash(key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]

    def _path(self, key: str) -> Path:
        return self.directory / f"{self._key_hash(key)}.json"

    def get(self, key: str) -> Optional[Any]:
        if not self.enabled:
            return None
        p = self._path(key)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text("utf-8"))
            if time.time() - data.get("_ts", 0) > self.ttl_seconds:
                p.unlink(missing_ok=True)
                return None
            return data.get("value")
        except Exception:
            return None

    def set(self, key: str, value: Any) -> None:
        if not self.enabled:
            return
        payload = {"_ts": time.time(), "value": value}
        self._path(key).write_text(
            json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8"
        )


# Module-level singleton
_cache: DiskCache | None = None


def get_cache() -> DiskCache:
    global _cache
    if _cache is None:
        _cache = DiskCache()
    return _cache
