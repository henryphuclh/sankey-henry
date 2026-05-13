import hashlib


def sha256_short(text: str, prefix_chars: int = 5000, length: int = 16) -> str:
    """Return a short SHA-256 hex digest of the first `prefix_chars` chars of text."""
    content = text[:prefix_chars].encode("utf-8", errors="replace")
    return hashlib.sha256(content).hexdigest()[:length]


def dict_hash(data: dict, length: int = 16) -> str:
    """Return a short SHA-256 hex digest of a dict (sorted keys for stability)."""
    import json
    serialized = json.dumps(data, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()[:length]
