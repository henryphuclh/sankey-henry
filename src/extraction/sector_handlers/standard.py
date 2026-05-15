"""Sector handler for standard companies (technology, energy, consumer, industrial, etc.)."""
from __future__ import annotations

import re
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Comprehensive XBRL concept map for non-financial, non-pharma companies.
#
# Design principles:
#   • "total_revenue", "gross_profit", "operating_income", "net_income",
#     "cogs", "rd_expense", "sga_expense", "interest_expense", "income_tax"
#     map directly to SegmentData fields.
#   • "_ga", "_selling", "_marketing" are component accumulator keys —
#     they are summed into sga_expense only when no combined SG&A tag is found.
#   • "_pretax" holds pre-tax income for downstream derivation.
#   • Priority: most-specific / most-common tag first in iteration, but the
#     parser keeps the largest-magnitude value per field to handle duplicates.
# ---------------------------------------------------------------------------
_STD: Dict[str, str] = {

    # ── Revenue ───────────────────────────────────────────────────────────────
    # ASC 606 (post-2018 US GAAP) — tech, consumer services, aerospace
    "RevenueFromContractWithCustomerExcludingAssessedTax":   "total_revenue",
    "RevenueFromContractWithCustomerIncludingAssessedTax":   "total_revenue",
    # Broad catch-all — energy, telecom, consumer staples, utilities
    "Revenues":                                              "total_revenue",
    # Pre-ASC 606 legacy (still seen in older 10-K filings)
    "SalesRevenueNet":                                       "total_revenue",
    # Explicit total label (some industrials, conglomerates)
    "TotalRevenues":                                         "total_revenue",
    # Retail & industrial style
    "NetSales":                                              "total_revenue",
    # Goods-only / services-only (older disaggregation, non-dimensional)
    "SalesRevenueGoodsNet":                                  "total_revenue",
    "SalesRevenueServicesNet":                               "total_revenue",
    # Miscellaneous
    "RevenueNet":                                            "total_revenue",
    # IFRS (20-F filers: ASML, SAP, Toyota, TSM, NVS, RHHBY)
    "Revenue":                                               "total_revenue",
    "RevenueFromContractsWithCustomers":                     "total_revenue",
    "RevenueFromSaleOfGoods":                                "total_revenue",   # IFRS pharma/consumer (NVS, Roche)
    "RevenueFromRenderingOfServices":                        "total_revenue",   # IFRS services
    "RevenueFromSaleOfGoodsAndRenderingOfServices":          "total_revenue",   # IFRS combined
    # Utilities (NEE, DUK, SO, AEP — regulated + unregulated operating revenue)
    "RegulatedAndUnregulatedOperatingRevenue":               "total_revenue",
    "RegulatedOperatingRevenue":                             "total_revenue",
    "UnregulatedOperatingRevenue":                           "total_revenue",
    "ElectricUtilityRevenue":                                "total_revenue",
    "OperatingRevenues":                                     "total_revenue",

    # ── COGS ──────────────────────────────────────────────────────────────────
    # Standard ASC 606 cost tag (tech, services)
    "CostOfRevenue":                                         "cogs",
    # Plural variant — Netflix, some media/streaming
    "CostOfRevenues":                                        "cogs",
    # Classic goods tag
    "CostOfGoodsSold":                                       "cogs",
    # Mixed goods + services
    "CostOfGoodsAndServicesSold":                            "cogs",
    # Boeing / aerospace style
    "CostOfGoodsSoldAndServicesSold":                        "cogs",
    # Pure-service companies
    "CostOfServices":                                        "cogs",
    # Retail (Walmart, Costco, Home Depot, TJX)
    "CostOfSales":                                           "cogs",
    # When D&A is separated out (capital-intensive industrials, utilities)
    "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization": "cogs",
    # Telecom (AT&T, Verizon)
    "CostOfServicesAndProducts":                             "cogs",
    # Media / direct production costs
    "DirectOperatingCosts":                                  "cogs",

    # ── Gross Profit ──────────────────────────────────────────────────────────
    "GrossProfit":                                           "gross_profit",
    "GrossProfitLoss":                                       "gross_profit",    # IFRS

    # ── R&D ───────────────────────────────────────────────────────────────────
    "ResearchAndDevelopmentExpense":                         "rd_expense",
    # Without acquired in-process R&D (biotech/pharma overlap companies)
    "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost": "rd_expense",
    # GE Aerospace, RTX, some industrials
    "ResearchDevelopmentAndRelatedExpenses":                 "rd_expense",

    # ── SG&A — combined line (preferred) ──────────────────────────────────────
    "SellingGeneralAndAdministrativeExpense":                "sga_expense",
    "SellingGeneralAdministrativeAndOtherExpense":           "sga_expense",

    # ── SG&A — components (accumulated only when combined line is absent) ─────
    # G&A component (e.g. Uber, Palantir, many SaaS companies)
    "GeneralAndAdministrativeExpense":                       "_ga",
    # IFRS G&A
    "AdministrativeExpenses":                                "_ga",
    # Selling / marketing components
    "SellingAndMarketingExpense":                            "_selling",
    "SellingExpense":                                        "_selling",
    "MarketingExpense":                                      "_marketing",
    "MarketingAndAdvertisingExpense":                        "_marketing",
    # IFRS selling / distribution
    "DistributionCosts":                                     "_selling",
    "SellingDistributionAndAdministrativeExpenses":          "sga_expense",  # IFRS combined

    # ── Operating Income ──────────────────────────────────────────────────────
    "OperatingIncomeLoss":                                   "operating_income",

    # ── Pre-tax income (derivation aid — NOT assigned to operating_income) ────
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": "_pretax",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments": "_pretax",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxes":   "_pretax",

    # ── Interest Expense ──────────────────────────────────────────────────────
    "InterestExpense":                                       "interest_expense",
    "InterestExpenseNonoperating":                           "interest_expense",
    "InterestAndDebtExpense":                                "interest_expense",
    "FinanceCosts":                                          "interest_expense",  # IFRS

    # ── Income Tax ────────────────────────────────────────────────────────────
    "IncomeTaxExpenseBenefit":                               "income_tax",

    # ── Net Income ────────────────────────────────────────────────────────────
    "NetIncomeLoss":                                         "net_income",
    "NetIncomeLossAvailableToCommonStockholdersBasic":       "net_income",
    "ProfitLoss":                                            "net_income",        # IFRS
    "NetIncomeLossAttributableToParent":                     "net_income",        # consolidated
}

# ── Direct-field keys (written straight to result dict) ─────────────────────
_DIRECT_FIELDS = frozenset((
    "total_revenue", "gross_profit", "operating_income", "net_income",
    "cogs", "rd_expense", "sga_expense", "interest_expense", "income_tax",
))


# ---------------------------------------------------------------------------
# LLM prompt hints
# ---------------------------------------------------------------------------
STANDARD_SEGMENT_HINTS = """
For this company, extract revenue and operating income for EACH distinct business segment
as reported in the filing's segment footnotes or Note on Segment Information.

Common segment types to look for:
- Product lines / Business units (e.g. Cloud, Consumer, Enterprise)
- Industry verticals (e.g. Aerospace, Defense, Industrial, Healthcare)
- Geographic regions (only if no product-level breakdown is available)
- Service vs Product split (e.g. Software vs Hardware, Services vs Equipment)

IMPORTANT NOTES:
- Use the MOST GRANULAR segment breakdown available in the filing.
- If the company reports both product segments AND geographic segments, prefer product/business unit.
- Do NOT include elimination entries or inter-segment adjustments as segments.
- For diversified companies, each major business division should be a separate segment node.
- If only one segment is reported (no breakdown), return that single segment as the total.

Always extract segment revenue figures that sum close to total reported revenue.
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_prompt_hints() -> str:
    return STANDARD_SEGMENT_HINTS


def normalize_segment_name(raw: str) -> str:
    return raw.strip().title()


def pnl_from_standard_filing(filing_obj) -> Dict[str, Any]:
    """Comprehensive XBRL P&L extraction for standard (non-financial, non-pharma) companies.

    Covers technology, energy, consumer, industrial, telecom, media, and utility sectors.
    Derives missing fields (gross profit, operating income, net income) when possible.
    """
    empty: Dict[str, Any] = {
        "total_revenue": None, "gross_profit": None, "operating_income": None,
        "net_income": None, "cogs": None, "rd_expense": None, "sga_expense": None,
        "interest_expense": None, "income_tax": None, "currency": "USD",
    }
    if filing_obj is None:
        return empty

    raw = _parse_concepts(filing_obj)

    result = dict(empty)

    # ── Direct assignments ────────────────────────────────────────────────────
    for field in _DIRECT_FIELDS:
        v = raw.get(field)
        if v is not None:
            result[field] = v

    # ── SG&A: sum components when no combined line was found ──────────────────
    if result["sga_expense"] is None:
        ga      = raw.get("_ga")       or 0.0
        selling = raw.get("_selling")  or 0.0
        mktg    = raw.get("_marketing") or 0.0
        total_comp = ga + selling + mktg
        if total_comp > 0:
            result["sga_expense"] = total_comp

    # ── Derive gross_profit = revenue − cogs ──────────────────────────────────
    if result["gross_profit"] is None:
        rev  = result["total_revenue"]
        cogs = result["cogs"]
        if rev is not None and cogs is not None and cogs > 0:
            result["gross_profit"] = rev - cogs

    # ── Derive operating_income = gross_profit − R&D − SG&A ──────────────────
    if result["operating_income"] is None:
        gp  = result["gross_profit"]
        rd  = result["rd_expense"]  or 0.0
        sga = result["sga_expense"] or 0.0
        if gp is not None and (rd > 0 or sga > 0):
            result["operating_income"] = gp - rd - sga

    # ── Operating income fallback: pre-tax + interest (approximate) ───────────
    # operating income ≈ pre-tax income + interest expense
    if result["operating_income"] is None:
        pretax = raw.get("_pretax")
        if pretax is not None:
            ie = result["interest_expense"] or 0.0
            result["operating_income"] = pretax + ie

    # ── Net income fallback: pre-tax − tax ───────────────────────────────────
    if result["net_income"] is None:
        pretax = raw.get("_pretax")
        tax    = result["income_tax"]
        if pretax is not None and tax is not None:
            result["net_income"] = pretax - abs(tax)
        elif pretax is not None:
            result["net_income"] = pretax

    return result


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _parse_concepts(filing_obj) -> Dict[str, float]:
    """Walk the income_statement DataFrame; return {field_name: best_value}."""
    result: Dict[str, float] = {}
    if filing_obj is None:
        return result

    try:
        stmt = filing_obj.income_statement
        df   = stmt.to_dataframe() if stmt is not None else None
    except Exception:
        return result

    if df is None or df.empty:
        return result

    period_cols = [c for c in df.columns if _is_period_col(c)]
    if not period_cols:
        return result
    latest_col = period_cols[0]  # edgartools orders newest-first

    # Keep only consolidated (non-dimensioned) rows
    totals = df
    if "is_breakdown" in totals.columns:
        totals = totals[totals["is_breakdown"].fillna(False) == False]
    if "dimension" in totals.columns:
        totals = totals[totals["dimension"].fillna(False) == False]

    if "concept" not in totals.columns:
        return result

    for _, row in totals.iterrows():
        concept_short = _strip_prefix(str(row.get("concept", "") or ""))
        field = _STD.get(concept_short)
        if not field:
            continue
        try:
            fval = float(row.get(latest_col))
        except (TypeError, ValueError):
            continue
        # Keep largest-magnitude value when concept appears in multiple rows
        existing = result.get(field)
        if existing is None or abs(fval) > abs(existing):
            result[field] = fval

    return result


def _strip_prefix(concept: str) -> str:
    """Remove XBRL namespace: 'us-gaap:NetIncomeLoss' → 'NetIncomeLoss'."""
    if ":" in concept:
        return concept.split(":", 1)[-1]
    # edgartools v5 underscore style: 'us-gaap_NetIncomeLoss', 'nvda_SomeCustom'
    m = re.match(r'^[a-z][a-z0-9\-]*_(.+)$', concept)
    if m:
        return m.group(1)
    return concept


def _is_period_col(col: Any) -> bool:
    """True if the column header looks like a fiscal period (contains year + FY/Q/-)."""
    s = str(col)
    return bool(re.search(r"\d{4}", s)) and any(t in s for t in ("FY", "Q", "-"))
