"""Sector handler for standard companies (technology, energy, consumer, industrial, etc.)."""
from __future__ import annotations

import re
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# US GAAP concept map  — applies to US_SEC filers (10-K / 10-Q)
#
# Design principles:
#   • Only us-gaap namespace concepts belong here.
#   • "_ga", "_selling", "_marketing" are component accumulator keys summed
#     into sga_expense when no combined SG&A tag is found.
#   • "_pretax" holds pre-tax income for downstream derivation.
#   • Priority: most-specific tag first; parser keeps the largest-magnitude
#     value per field to handle duplicates.
# ---------------------------------------------------------------------------
_STD_USGAAP: Dict[str, str] = {

    # ── Revenue ───────────────────────────────────────────────────────────────
    "RevenueFromContractWithCustomerExcludingAssessedTax":   "total_revenue",  # ASC 606 primary
    "RevenueFromContractWithCustomerIncludingAssessedTax":   "total_revenue",
    "Revenues":                                              "total_revenue",  # broad catch-all
    "SalesRevenueNet":                                       "total_revenue",  # pre-ASC 606 legacy
    "TotalRevenues":                                         "total_revenue",
    "NetSales":                                              "total_revenue",  # retail / industrial
    "SalesRevenueGoodsNet":                                  "total_revenue",
    "SalesRevenueServicesNet":                               "total_revenue",
    "RevenueNet":                                            "total_revenue",
    "RegulatedAndUnregulatedOperatingRevenue":               "total_revenue",  # utilities (NEE, DUK)
    "RegulatedOperatingRevenue":                             "total_revenue",
    "UnregulatedOperatingRevenue":                           "total_revenue",
    "ElectricUtilityRevenue":                                "total_revenue",
    "OperatingRevenues":                                     "total_revenue",  # telecom / utilities

    # ── COGS ──────────────────────────────────────────────────────────────────
    "CostOfRevenue":                                         "cogs",
    "CostOfRevenues":                                        "cogs",
    "CostOfGoodsSold":                                       "cogs",
    "CostOfGoodsAndServicesSold":                            "cogs",
    "CostOfGoodsSoldAndServicesSold":                        "cogs",  # Boeing / aerospace
    "CostOfServices":                                        "cogs",
    "CostOfSales":                                           "cogs",  # retail (Walmart, Costco)
    "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization": "cogs",
    "CostOfServicesAndProducts":                             "cogs",  # telecom
    "DirectOperatingCosts":                                  "cogs",  # media

    # ── Gross Profit ──────────────────────────────────────────────────────────
    "GrossProfit":                                           "gross_profit",

    # ── R&D ───────────────────────────────────────────────────────────────────
    "ResearchAndDevelopmentExpense":                         "rd_expense",
    "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost": "rd_expense",
    "ResearchDevelopmentAndRelatedExpenses":                 "rd_expense",  # GE, RTX

    # ── SG&A — combined line (preferred) ──────────────────────────────────────
    "SellingGeneralAndAdministrativeExpense":                "sga_expense",
    "SellingGeneralAdministrativeAndOtherExpense":           "sga_expense",

    # ── SG&A — components (accumulated only when combined line is absent) ─────
    "GeneralAndAdministrativeExpense":                       "_ga",
    "SellingAndMarketingExpense":                            "_selling",
    "SellingExpense":                                        "_selling",
    "MarketingExpense":                                      "_marketing",
    "MarketingAndAdvertisingExpense":                        "_marketing",

    # ── Operating Income — US GAAP ────────────────────────────────────────────
    "OperatingIncomeLoss":                                   "operating_income",

    # ── Pre-tax Income — US GAAP ──────────────────────────────────────────────
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": "_pretax",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments": "_pretax",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxes":   "_pretax",

    # ── Interest Expense — US GAAP ────────────────────────────────────────────
    "InterestExpense":                                       "interest_expense",
    "InterestExpenseNonoperating":                           "interest_expense",
    "InterestAndDebtExpense":                                "interest_expense",

    # ── Income Tax — US GAAP ──────────────────────────────────────────────────
    "IncomeTaxExpenseBenefit":                               "income_tax",

    # ── Net Income — US GAAP ──────────────────────────────────────────────────
    "NetIncomeLoss":                                         "net_income",
    "NetIncomeLossAvailableToCommonStockholdersBasic":       "net_income",
    "NetIncomeLossAttributableToParent":                     "net_income",
    "ProfitLoss":                                            "net_income",  # GAAP consolidated
}


# ---------------------------------------------------------------------------
# IFRS concept map  — applies to INTL_SEC filers (20-F, using ifrs-full)
#
# Design principles:
#   • Only ifrs-full / ifrs15-full / company-specific IFRS concepts belong here.
#   • Deliberately excludes us-gaap concepts to prevent cross-standard conflicts.
# ---------------------------------------------------------------------------
_STD_IFRS: Dict[str, str] = {

    # ── Revenue — IFRS ────────────────────────────────────────────────────────
    "Revenue":                                               "total_revenue",  # ifrs-full primary
    "RevenueFromContractsWithCustomers":                     "total_revenue",  # IFRS 15
    "RevenueFromSaleOfGoods":                                "total_revenue",
    "RevenueFromRenderingOfServices":                        "total_revenue",
    "RevenueFromSaleOfGoodsAndRenderingOfServices":          "total_revenue",

    # ── COGS — IFRS ───────────────────────────────────────────────────────────
    "CostOfSales":                                           "cogs",           # ifrs-full:CostOfSales
    "CostOfRevenue":                                         "cogs",           # some IFRS filers

    # ── Gross Profit — IFRS ───────────────────────────────────────────────────
    "GrossProfit":                                           "gross_profit",
    "GrossProfitLoss":                                       "gross_profit",

    # ── R&D — IFRS ────────────────────────────────────────────────────────────
    "ResearchAndDevelopmentExpense":                         "rd_expense",     # ifrs-full variant

    # ── SG&A — IFRS combined ──────────────────────────────────────────────────
    "SellingGeneralAndAdministrativeExpense":                "sga_expense",    # some IFRS filers
    "SellingDistributionAndAdministrativeExpenses":          "sga_expense",    # SAP, ASML

    # ── SG&A — IFRS components ────────────────────────────────────────────────
    "AdministrativeExpenses":                                "_ga",            # ifrs-full G&A
    "DistributionCosts":                                     "_selling",       # ifrs-full selling
    "SellingAndMarketingExpense":                            "_selling",

    # ── Operating Income — IFRS ───────────────────────────────────────────────
    "OperatingProfit":                                       "operating_income",
    "OperatingProfitLoss":                                   "operating_income",
    "ProfitLossFromOperatingActivities":                     "operating_income",
    "ProfitFromOperations":                                  "operating_income",
    "ResultFromOperatingActivities":                         "operating_income",

    # ── Pre-tax Income — IFRS ─────────────────────────────────────────────────
    "ProfitLossBeforeTax":                                   "_pretax",
    "ProfitBeforeTax":                                       "_pretax",
    "ProfitBeforeIncomeTax":                                 "_pretax",

    # ── Interest Expense — IFRS ───────────────────────────────────────────────
    "FinanceCosts":                                          "interest_expense",

    # ── Income Tax — IFRS ─────────────────────────────────────────────────────
    "IncomeTaxes":                                           "income_tax",
    "TaxExpenseIncome":                                      "income_tax",
    "IncomeTaxExpenseBenefit":                               "income_tax",     # some IFRS filers use GAAP tag

    # ── Net Income — IFRS ─────────────────────────────────────────────────────
    "ProfitLoss":                                            "net_income",     # ifrs-full primary
    "ProfitAttributableToOwnersOfParent":                    "net_income",     # consolidated parent
    "NetIncomeLoss":                                         "net_income",     # some IFRS filers
}


# ── Direct-field keys (written straight to result dict) ─────────────────────
_DIRECT_FIELDS = frozenset((
    "total_revenue", "gross_profit", "operating_income", "net_income",
    "cogs", "rd_expense", "sga_expense", "interest_expense", "income_tax",
))



def pnl_from_standard_filing(
    filing_obj,
    data_source: str = "US_SEC",
) -> Dict[str, Any]:
    """XBRL P&L extraction for standard companies.

    US_SEC → US GAAP map. INTL_SEC → auto-detects IFRS vs US GAAP from namespace prefix counts.
    """
    empty: Dict[str, Any] = {
        "total_revenue": None, "gross_profit": None, "operating_income": None,
        "net_income": None, "cogs": None, "rd_expense": None, "sga_expense": None,
        "interest_expense": None, "income_tax": None, "currency": "USD",
    }
    if filing_obj is None:
        return empty

    if data_source == "INTL_SEC":
        concept_map = _STD_IFRS if _detect_xbrl_standard(filing_obj) == "IFRS" else _STD_USGAAP
    else:
        concept_map = _STD_USGAAP
    raw = _parse_concepts(filing_obj, concept_map)

    result = dict(empty)

    # Expenses stored as positive; XBRL filers sometimes report them negative.
    _EXPENSE_FIELDS = frozenset(("cogs", "rd_expense", "sga_expense", "interest_expense", "income_tax"))
    for field in _DIRECT_FIELDS:
        v = raw.get(field)
        if v is not None:
            result[field] = abs(v) if field in _EXPENSE_FIELDS else v

    # SG&A: sum components when no combined line was found
    if result["sga_expense"] is None:
        total_comp = abs(raw.get("_ga") or 0.0) + abs(raw.get("_selling") or 0.0) + abs(raw.get("_marketing") or 0.0)
        if total_comp > 0:
            result["sga_expense"] = total_comp

    if result["gross_profit"] is None:
        rev, cogs = result["total_revenue"], result["cogs"]
        if rev is not None and cogs is not None and cogs > 0:
            result["gross_profit"] = rev - cogs

    if result["operating_income"] is None:
        gp  = result["gross_profit"]
        rd  = result["rd_expense"]  or 0.0
        sga = result["sga_expense"] or 0.0
        if gp is not None and (rd > 0 or sga > 0):
            result["operating_income"] = gp - rd - sga

    # Fallback: operating_income ≈ pretax + interest
    if result["operating_income"] is None:
        pretax = raw.get("_pretax")
        if pretax is not None:
            result["operating_income"] = pretax + (result["interest_expense"] or 0.0)

    if result["net_income"] is None:
        pretax = raw.get("_pretax")
        tax    = result["income_tax"]
        if pretax is not None and tax is not None:
            result["net_income"] = pretax - abs(tax)
        elif pretax is not None:
            result["net_income"] = pretax

    return result


def _detect_xbrl_standard(filing_obj) -> str:
    """Infer GAAP vs IFRS from namespace prefix counts. Some 20-F filers use us-gaap (e.g. ASML)."""
    try:
        stmt = filing_obj.income_statement
        df   = stmt.to_dataframe() if stmt is not None else None
    except Exception:
        return "IFRS"
    if df is None or df.empty or "concept" not in df.columns:
        return "IFRS"
    usgaap = sum(1 for c in df["concept"].fillna("") if str(c).startswith("us-gaap"))
    ifrs   = sum(1 for c in df["concept"].fillna("") if str(c).startswith("ifrs"))
    return "US_GAAP" if usgaap >= ifrs else "IFRS"


def _parse_concepts(filing_obj, concept_map: Dict[str, str]) -> Dict[str, float]:
    """Walk income_statement DataFrame → {field_name: best_value}."""
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
        field = concept_map.get(concept_short)
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
