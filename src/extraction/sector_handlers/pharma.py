"""Sector handler for pharmaceutical and biotech companies."""


PHARMA_SEGMENT_HINTS = """
For this pharmaceutical/biotech company, extract revenue by:

1. THERAPEUTIC AREAS (primary): Oncology, Immunology/Inflammation, Cardiovascular,
   Neuroscience/CNS, Diabetes/Metabolic, Infectious Disease, Rare Disease, Ophthalmology
2. NAMED PRODUCTS: Individual drug revenues if disclosed (e.g., Keytruda, Humira, Ozempic)
3. GEOGRAPHY: US, International/Ex-US, Emerging Markets (if segment reporting uses geography)
4. BUSINESS LINES: Pharmaceuticals vs Diagnostics, Innovative vs Established/Generic

For pharma P&L, key cost items:
- Cost of Sales / Cost of Goods Sold (COGS): manufacturing costs
- Research & Development (R&D): clinical trials, discovery
- Selling, General & Administrative (SG&A): marketing, sales force

IMPORTANT: R&D expense is typically 15-25% of revenue for large pharma — a major cost node.
If a company reports both product segments AND geographic segments, prefer product/therapeutic area.
"""

CANONICAL_THERAPEUTIC_AREAS = {
    "oncology": "Oncology",
    "cancer": "Oncology",
    "hematology": "Oncology",
    "immunology": "Immunology & Inflammation",
    "inflammation": "Immunology & Inflammation",
    "rheumatology": "Immunology & Inflammation",
    "cardiovascular": "Cardiovascular",
    "cardiology": "Cardiovascular",
    "neuroscience": "Neuroscience",
    "neurology": "Neuroscience",
    "cns": "Neuroscience",
    "psychiatry": "Neuroscience",
    "diabetes": "Diabetes & Metabolism",
    "metabolism": "Diabetes & Metabolism",
    "metabolic": "Diabetes & Metabolism",
    "obesity": "Diabetes & Metabolism",
    "infectious": "Infectious Disease & Vaccines",
    "vaccines": "Infectious Disease & Vaccines",
    "virology": "Infectious Disease & Vaccines",
    "rare disease": "Rare Disease",
    "rare": "Rare Disease",
    "ophthalmology": "Ophthalmology",
    "eye": "Ophthalmology",
    "diagnostics": "Diagnostics",
    "established": "Established Medicines",
    "generic": "Established Medicines",
}


def get_prompt_hints() -> str:
    return PHARMA_SEGMENT_HINTS


_PHARMA_RD_MIN_PCT = 0.05  # R&D must be ≥ 5% of revenue to confirm pharma structure


def has_pharma_indicators(pnl: dict, total_rev: float) -> bool:
    """Return True if extracted P&L shows pharma R&D structure.

    Called after sector-specific extraction to validate that R&D expense was
    actually found.  If False, caller should fall back to standard handler.
    """
    if not total_rev or total_rev <= 0:
        return False
    rd = pnl.get("rd_expense")
    # Use abs() — IFRS filers (NVS, RHHBY) store expenses as negative values
    return rd is not None and rd != 0 and abs(rd) / abs(total_rev) >= _PHARMA_RD_MIN_PCT


def normalize_segment_name(raw: str) -> str:
    lower = raw.lower().strip()
    for key, canonical in CANONICAL_THERAPEUTIC_AREAS.items():
        if key in lower:
            return canonical
    return raw.title()


def pnl_from_pharma_filing(filing_obj) -> dict:
    """Pharma P&L — uses the comprehensive standard XBRL extractor.

    R&D is captured via ResearchAndDevelopmentExpense (and its variants)
    which are included in the standard concept map.
    """
    from src.extraction.sector_handlers.standard import pnl_from_standard_filing
    return pnl_from_standard_filing(filing_obj)
