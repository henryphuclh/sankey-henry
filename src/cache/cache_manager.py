import json
import time
from pathlib import Path
from typing import Any, Optional

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import CACHE_DIR, CACHE_TTL


class CacheManager:
    """File-based cache with per-namespace TTL."""

    def __init__(self, base_dir: Path = CACHE_DIR):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, namespace: str, key: str) -> Path:
        safe_key = key.replace("/", "_").replace("\\", "_").replace(":", "_")
        ns_dir = self.base_dir / namespace
        ns_dir.mkdir(parents=True, exist_ok=True)
        return ns_dir / f"{safe_key}.json"

    def _ttl_seconds(self, namespace: str) -> float:
        days = CACHE_TTL.get(namespace, 7)
        return days * 86400

    def get(self, namespace: str, key: str) -> Optional[Any]:
        path = self._path(namespace, key)
        if not path.exists():
            return None
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
            age = time.time() - meta.get("timestamp", 0)
            if age > self._ttl_seconds(namespace):
                path.unlink(missing_ok=True)
                return None
            return meta["data"]
        except (json.JSONDecodeError, KeyError):
            path.unlink(missing_ok=True)
            return None

    def set(self, namespace: str, key: str, data: Any) -> None:
        path = self._path(namespace, key)
        payload = {"timestamp": time.time(), "data": data}
        path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")

    def invalidate(self, namespace: str, key: str) -> None:
        self._path(namespace, key).unlink(missing_ok=True)

    def invalidate_ticker(self, ticker: str) -> None:
        """Remove all cached data for a ticker across relevant namespaces."""
        for namespace in ("segments", "llm", "xbrl", "filings"):
            ns_dir = self.base_dir / namespace / ticker
            if ns_dir.exists():
                for f in ns_dir.glob("*.json"):
                    f.unlink(missing_ok=True)
            # also flat files named after ticker
            flat = self._path(namespace, ticker)
            flat.unlink(missing_ok=True)

    def exists(self, namespace: str, key: str) -> bool:
        return self.get(namespace, key) is not None


cache = CacheManager()
