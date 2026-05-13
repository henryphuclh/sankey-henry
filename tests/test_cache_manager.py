"""Tests for cache manager."""
import sys, time, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.cache.cache_manager import CacheManager


def test_set_and_get():
    with tempfile.TemporaryDirectory() as tmpdir:
        cm = CacheManager(Path(tmpdir))
        cm.set("test", "key1", {"value": 42})
        result = cm.get("test", "key1")
        assert result == {"value": 42}


def test_missing_key_returns_none():
    with tempfile.TemporaryDirectory() as tmpdir:
        cm = CacheManager(Path(tmpdir))
        assert cm.get("test", "nonexistent") is None


def test_invalidate():
    with tempfile.TemporaryDirectory() as tmpdir:
        cm = CacheManager(Path(tmpdir))
        cm.set("test", "key1", {"value": 1})
        cm.invalidate("test", "key1")
        assert cm.get("test", "key1") is None


def test_corrupt_file_returns_none():
    with tempfile.TemporaryDirectory() as tmpdir:
        cm = CacheManager(Path(tmpdir))
        p = cm._path("test", "bad")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("not valid json")
        assert cm.get("test", "bad") is None


def test_exists():
    with tempfile.TemporaryDirectory() as tmpdir:
        cm = CacheManager(Path(tmpdir))
        assert not cm.exists("test", "k")
        cm.set("test", "k", {"x": 1})
        assert cm.exists("test", "k")
