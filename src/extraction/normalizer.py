"""Normalize segment names across multiple periods for a single ticker."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import DATA_DIR
from src.extraction.models import SegmentData

try:
    from rapidfuzz import fuzz, process
    _HAS_RAPIDFUZZ = True
except ImportError:
    _HAS_RAPIDFUZZ = False


_CANONICAL_DIR = DATA_DIR / "segments"
_CANONICAL_DIR.mkdir(parents=True, exist_ok=True)

# Fuzzy match threshold — names with ratio above this are considered the same segment
_MATCH_THRESHOLD = 80


def normalize_segments(
    periods: List[SegmentData],
    ticker:  str,
) -> List[SegmentData]:
    """
    Align segment names across all periods so that the same business unit
    has a consistent name throughout the timeline.

    Strategy:
    1. Collect all unique raw segment names across all periods.
    2. Build a canonical map using fuzzy matching.
    3. Apply the map to all SegmentData objects.
    4. Persist the map for transparency.
    """
    if not periods:
        return periods

    # Collect all raw names
    all_names: List[str] = []
    for sd in periods:
        for seg in sd.segments:
            if seg.segment_name not in all_names:
                all_names.append(seg.segment_name)

    if not all_names:
        return periods

    # Build canonical map
    canonical_map = _build_canonical_map(all_names, ticker)

    # Apply map
    for sd in periods:
        for seg in sd.segments:
            seg.segment_name = canonical_map.get(seg.segment_name, seg.segment_name)

    # Merge duplicate segments (same canonical name, same period)
    for sd in periods:
        sd.segments = _merge_duplicates(sd.segments)

    return periods


def _build_canonical_map(names: List[str], ticker: str) -> Dict[str, str]:
    """
    Build {raw_name: canonical_name} using fuzzy clustering.
    The first (longest/most descriptive) name in each cluster becomes canonical.
    """
    # Load existing map if present
    map_path = _CANONICAL_DIR / f"{ticker}_canonical.json"
    if map_path.exists():
        try:
            return json.loads(map_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    canonical_map: Dict[str, str] = {}
    canonical_names: List[str] = []

    for name in sorted(names, key=len, reverse=True):  # longer names are more descriptive
        if name in canonical_map:
            continue

        if not canonical_names or not _HAS_RAPIDFUZZ:
            canonical_names.append(name)
            canonical_map[name] = name
            continue

        # Find best match among existing canonical names
        best_match, score, _ = process.extractOne(
            name, canonical_names, scorer=fuzz.token_sort_ratio
        )
        if score >= _MATCH_THRESHOLD:
            canonical_map[name] = best_match
        else:
            canonical_names.append(name)
            canonical_map[name] = name

    # Persist for inspection
    try:
        map_path.write_text(json.dumps(canonical_map, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

    return canonical_map


def _merge_duplicates(segments: List) -> List:
    """Sum values for segments with the same canonical name within a period."""
    merged: Dict[str, object] = {}
    for seg in segments:
        if seg.segment_name not in merged:
            merged[seg.segment_name] = seg
        else:
            existing = merged[seg.segment_name]
            existing.value = (existing.value or 0) + (seg.value or 0)
    return list(merged.values())
