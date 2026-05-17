"""Sector handler for pharmaceutical and biotech companies."""
from __future__ import annotations

from typing import List

_PHARMA_RD_MIN_PCT   = 0.05   # R&D ≥ 5% of revenue confirms pharma structure
_PHARMA_MAX_SEGMENTS = 10     # XBRL returning > 10 items = drug-level, too granular


# ── Indicators ────────────────────────────────────────────────────────────────

def has_pharma_indicators(pnl: dict, total_rev: float) -> bool:
    """Return True if R&D expense confirms pharma P&L structure (≥ 5% of revenue)."""
    if not total_rev or total_rev <= 0:
        return False
    rd = pnl.get("rd_expense")
    return rd is not None and rd != 0 and abs(rd) / abs(total_rev) >= _PHARMA_RD_MIN_PCT


def is_too_granular(segments: list) -> bool:
    """Return True when XBRL returned individual drug products instead of therapeutic areas.

    Pharma XBRL sometimes exposes 20+ individual drug entries (Keytruda, Humira, Ozempic…)
    rather than the therapeutic-area groupings shown in the Segment note. When this happens,
    the LLM extractor produces a better Sankey — so signal the router to use LLM instead.
    """
    return len(segments) > _PHARMA_MAX_SEGMENTS


# ── LLM output cleanup ────────────────────────────────────────────────────────

_THERAPEUTIC_AREA_TERMS = frozenset({
    "oncology", "immunology", "neuroscience", "neurology", "psychiatry",
    "cardiovascular", "cardiology", "infectious", "virology", "hematology",
    "diabetes", "obesity", "metabolic", "endocrinology",
    "rare disease", "rare", "genetic", "orphan",
    "aesthetics", "dermatology", "ophthalmology", "eye care",
    "rheumatology", "gastroenterology", "pulmonology", "respiratory",
    "women", "urology", "renal", "transplant",
})


def _is_therapeutic_area(name: str) -> bool:
    low = name.lower()
    return any(term in low for term in _THERAPEUTIC_AREA_TERMS)


def strip_product_level_from_llm(segs: list) -> list:
    """Remove individual drug products when therapeutic-area segments also exist.

    LLMs reading pharma revenue notes sometimes return BOTH therapeutic-area groupings
    (e.g. Oncology, Immunology) AND individual drug products (e.g. Keytruda, Humira)
    from the same note. When therapeutic-area items are present, drop the product-level
    ones so the Sankey stays at a meaningful level of granularity.
    """
    if not segs:
        return segs
    therapeutic = [s for s in segs if _is_therapeutic_area(s.segment_name)]
    if therapeutic and len(therapeutic) < len(segs):
        return therapeutic
    return segs
