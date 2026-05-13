"""Sector handler: banks, insurance, and financial services companies.

Provides:
  pnl_from_financial_filing(filing_obj)  -- auto-detect bank vs insurance, return P&L dict
  pnl_from_bank_filing(filing_obj)       -- bank-specific P&L with expense sub-components
  get_prompt_hints()                     -- LLM system-prompt additions for financial filings
  normalize_segment_name(raw)            -- canonicalize segment label variations
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# XBRL concept → internal field mapping
# Order within each group: most-specific / most-direct first.
# ---------------------------------------------------------------------------
_BANK_CONCEPTS: Dict[str, str] = {
    # ── Net revenue (direct single-line — preferred, avoids double-count) ──
    "RevenuesNetOfInterestExpense":                              "net_revenue_direct",
    "BankingRevenues":                                          "net_revenue_direct",
    "NetRevenues":                                              "net_revenue_direct",
    "TotalNetRevenues":                                         "net_revenue_direct",
    "TotalRevenues":                                            "net_revenue_direct",
    # SCHW-specific
    "NetRevenue":                                               "net_revenue_direct",
    "TotalNetRevenue":                                          "net_revenue_direct",
    # Standard US-GAAP revenue: used as total net revenue by C, SCHW, AXP
    "Revenues":                                                 "net_revenue_direct",
    "SalesRevenueNet":                                          "net_revenue_direct",

    # ── Net interest income (direct) ──
    "InterestIncomeExpenseNet":                                 "nii_direct",
    "NetInterestIncome":                                        "nii_direct",
    "InterestAndDividendIncomeOperatingNet":                    "nii_direct",
    # Components used to compute NII when direct tag absent
    "InterestAndDividendIncomeOperating":                       "interest_income_gross",
    "InterestIncomeOperating":                                  "interest_income_gross",
    "InterestAndFeeIncomeLoansAndLeases":                       "interest_income_loans",
    "InterestExpense":                                          "interest_expense_gross",
    "InterestExpenseDeposits":                                  "interest_expense_gross",
    "InterestExpenseOperating":                                 "interest_expense_gross",

    # ── Non-interest / fee income ──
    "NoninterestIncome":                                        "noninterest_income",
    "NonInterestIncome":                                        "noninterest_income",
    "TotalNoninterestIncome":                                   "noninterest_income",
    "OtherNoninterestIncome":                                   "noninterest_income",
    # Card-company revenue (AXP, V, MA)
    "RevenueFromContractWithCustomerExcludingAssessedTax":      "noninterest_income",
    "RevenueFromContractWithCustomerIncludingAssessedTax":      "noninterest_income",

    # ── Provision for credit losses ──
    "ProvisionForLoanLeaseAndOtherLosses":                      "provision_credit_loss",
    "ProvisionForLoanAndLeaseLosses":                           "provision_credit_loss",
    "CreditLossExpenseReversal":                                "provision_credit_loss",
    "ProvisionForCreditLoss":                                   "provision_credit_loss",
    "ProvisionForCreditLosses":                                 "provision_credit_loss",
    "ProvisionForDoubtfulAccounts":                             "provision_credit_loss",
    # Citi-specific provision concepts
    "FinancingReceivableExcludingAccruedInterestCreditLossExpenseReversal": "provision_credit_loss",
    "ProvisionForCreditLossBenefitsAndClaimsExpenseReversal":   "provision_credit_loss",
    "OffBalanceSheetCreditLossLiabilityCreditLossExpenseReversal": "provision_credit_loss",

    # ── Non-interest expense (total operating costs for banks) ──
    "NoninterestExpense":                                       "noninterest_expense",
    "NonInterestExpense":                                       "noninterest_expense",
    "TotalNoninterestExpense":                                  "noninterest_expense",
    "OperatingExpenses":                                        "noninterest_expense",

    # ── Expense sub-components ──
    # Personnel / compensation
    "LaborAndRelatedExpense":                                   "expense_personnel",
    "CompensationExpenseExcludingCostOfGoodAndServiceSold":     "expense_personnel",
    "EmployeeBenefitsAndShareBasedCompensation":                "expense_personnel",
    "CompensationAndBenefits":                                  "expense_personnel",  # GS
    "SalariesAndEmployeeBenefits":                              "expense_personnel",
    # Technology
    "InformationTechnologyAndDataProcessing":                   "expense_technology",
    "EquipmentExpense":                                         "expense_technology",
    "TechnologyCommunicationsAndEquipmentExpense":              "expense_technology",  # Citi
    "CommunicationsAndTechnology":                              "expense_technology",
    "CommunicationsAndInformationTechnology":                   "expense_technology",  # JPM
    # Occupancy
    "OccupancyNet":                                             "expense_occupancy",
    "PremisesAndEquipmentExpense":                              "expense_occupancy",
    "OccupancyAndEquipmentExpense":                             "expense_occupancy",
    # Professional services
    "ProfessionalAndContractServicesExpense":                   "expense_professional",
    "ProfessionalFees":                                         "expense_professional",
    "BrokerageClearanceExchangeAndDistributionFees":            "expense_professional",  # GS/MS
    # Marketing
    "MarketingAndAdvertisingExpense":                           "expense_marketing",
    "MarketingExpense":                                         "expense_marketing",
    # Residual "other" expense line — present in almost all banks
    "OtherNoninterestExpense":                                  "expense_other",
    "OtherExpenses":                                            "expense_other",
    "BusinessDevelopmentExpense":                               "expense_other",
    # AXP-specific
    "CardMemberServicesExpense":                                "expense_card_services",
    "RewardsExpense":                                           "expense_card_services",

    # ── Operating / pre-tax income ──
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": "pretax_income",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments": "pretax_income",
    "OperatingIncomeLoss":                                      "operating_income",
    "IncomeLossFromContinuingOperations":                       "operating_income",

    # ── Net income / tax ──
    "NetIncomeLoss":                                            "net_income",
    "NetIncomeLossAvailableToCommonStockholdersBasic":          "net_income",
    "ProfitLoss":                                               "net_income",
    "IncomeTaxExpenseBenefit":                                  "income_tax",

    # ── Insurance-specific ──
    "PremiumsEarnedNet":                                        "insurance_premiums",
    "NetPremiumsEarned":                                        "insurance_premiums",
    "PolicyholderBenefitsAndClaimsIncurredNet":                 "insurance_benefits",
    "BenefitsLossesAndExpenses":                                "insurance_benefits",
    "InsuranceLossesAndLossAdjustmentExpenses":                 "insurance_benefits",
}

# ---------------------------------------------------------------------------
# LLM prompt hints
# ---------------------------------------------------------------------------
FINANCIAL_SEGMENT_HINTS = """
For this financial company, identify the main business segments and their net revenues.

Revenue structure for banks:
  Net Revenue = Net Interest Income (NII) + Non-Interest Income
  NII = Interest Income − Interest Expense
  Non-Interest Income: service charges, card fees, advisory, underwriting, trading, asset management

Key P&L items to capture:
  1. Net Interest Income
  2. Non-Interest Income (fee income)
  3. Provision for Credit Losses (subtracted from revenue)
  4. Non-Interest Expense (total operating costs)
  5. Pre-tax Income = Net Revenue − Provision − Non-Interest Expense
  6. Income Tax
  7. Net Income

Typical bank segment names:
  Consumer Banking / Retail Banking / Personal Banking
  Commercial Banking / Corporate Banking / Middle Market
  Corporate & Investment Banking / Capital Markets / Global Banking & Markets
  Wealth & Asset Management / Private Banking
  Corporate / Treasury / Other

For insurance companies:
  Property & Casualty Insurance
  Life & Health Insurance
  Asset Management
  Corporate / Other

IMPORTANT:
- Report segment revenues in millions USD.
- For banks, segment revenue = NII + Non-Interest Income allocated to that segment.
- Do NOT include Provision for Credit Losses in revenue.
- Do NOT double-count interest income gross and NII.
"""

# ---------------------------------------------------------------------------
# Canonical segment name mapping
# ---------------------------------------------------------------------------
CANONICAL_SEGMENT_NAMES: Dict[str, str] = {
    "consumer banking":                    "Consumer Banking",
    "retail banking":                      "Consumer Banking",
    "personal banking":                    "Consumer Banking",
    "consumer & community banking":        "Consumer Banking",
    "consumer and community banking":      "Consumer Banking",
    "consumer & small business banking":   "Consumer Banking",
    "commercial banking":                  "Commercial Banking",
    "corporate banking":                   "Commercial Banking",
    "wholesale banking":                   "Commercial Banking",
    "middle market banking":               "Commercial Banking",
    "corporate & investment bank":         "Corporate & Investment Banking",
    "corporate and investment banking":    "Corporate & Investment Banking",
    "institutional securities":            "Corporate & Investment Banking",
    "global banking & markets":            "Corporate & Investment Banking",
    "global banking and markets":          "Corporate & Investment Banking",
    "corporate & institutional banking":   "Corporate & Investment Banking",
    "investment banking":                  "Investment Banking & Markets",
    "capital markets":                     "Investment Banking & Markets",
    "global markets":                      "Investment Banking & Markets",
    "markets":                             "Investment Banking & Markets",
    "trading":                             "Investment Banking & Markets",
    "wealth management":                   "Wealth & Asset Management",
    "asset management":                    "Wealth & Asset Management",
    "private banking":                     "Wealth & Asset Management",
    "global wealth & investment management": "Wealth & Asset Management",
    "investment management":               "Wealth & Asset Management",
    "wealth & investment management":      "Wealth & Asset Management",
    "property & casualty":                 "Property & Casualty Insurance",
    "life & health":                       "Life & Health Insurance",
    "treasury":                            "Corporate & Other",
    "corporate":                           "Corporate & Other",
    "other":                               "Corporate & Other",
    "corporate center":                    "Corporate & Other",
    "corporate and other":                 "Corporate & Other",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_prompt_hints() -> str:
    return FINANCIAL_SEGMENT_HINTS


def normalize_segment_name(raw: str) -> str:
    lower = raw.lower().strip()
    for key, canonical in CANONICAL_SEGMENT_NAMES.items():
        if key in lower:
            return canonical
    return raw.title()


def pnl_from_financial_filing(filing_obj) -> Dict[str, Any]:
    """Auto-detect bank vs insurance, dispatch to the right handler."""
    company_type = _detect_financial_type(filing_obj)
    if company_type == "insurance":
        return _pnl_from_insurance_filing(filing_obj)
    return pnl_from_bank_filing(filing_obj)


def pnl_from_bank_filing(filing_obj) -> Dict[str, Any]:
    """
    Extract bank P&L from a TenK / TenQ filing object.

    Returns a dict whose keys match SegmentData fields, plus bank-specific
    extras ('nii', 'noninterest_income', 'provision_credit_loss',
    'noninterest_expense', 'expense_detail').

    expense_detail is a JSON-serialisable dict {label: value} stored as
    'EXPENSE_DETAIL:{...}' in sd.notes so sankey_builder can render
    expense sub-components as individual nodes.
    """
    empty: Dict[str, Any] = {
        "total_revenue": None,
        "gross_profit": None,
        "operating_income": None,
        "net_income": None,
        "cogs": None,
        "rd_expense": None,
        "sga_expense": None,
        "interest_expense": None,
        "income_tax": None,
        "currency": "USD",
        # bank extras
        "nii": None,
        "noninterest_income": None,
        "provision_credit_loss": None,
        "noninterest_expense": None,
        "expense_detail": None,
    }

    if filing_obj is None:
        return empty

    raw = _parse_bank_concepts(filing_obj)

    # ── Resolve total (net) revenue ────────────────────────────────────────
    net_rev_direct = raw.get("net_revenue_direct")

    nii = raw.get("nii_direct")
    if nii is None:
        ig = raw.get("interest_income_gross")
        ie = raw.get("interest_expense_gross")
        if ig is not None and ie is not None:
            nii = ig - ie

    noninterest = raw.get("noninterest_income")

    if net_rev_direct and net_rev_direct > 0:
        total_revenue = net_rev_direct
    elif nii is not None and noninterest is not None:
        total_revenue = nii + noninterest
    elif nii is not None:
        total_revenue = nii
    elif noninterest is not None:
        total_revenue = noninterest
    else:
        total_revenue = None

    # ── Provision ─────────────────────────────────────────────────────────
    provision = raw.get("provision_credit_loss")
    if provision is not None:
        provision = abs(provision)  # always positive

    # ── Revenue after provision (bank's gross-profit analog) ──────────────
    rev_after_provision: Optional[float] = None
    if total_revenue is not None and provision is not None:
        rev_after_provision = total_revenue - provision
    elif total_revenue is not None:
        rev_after_provision = total_revenue

    # ── Non-interest expense ──────────────────────────────────────────────
    nie = raw.get("noninterest_expense")

    # Sanity check: NIE should not exceed total revenue (would indicate
    # a wrong concept is being captured, e.g. gross instead of net).
    if nie is not None and total_revenue and nie > total_revenue * 0.99:
        # Try to reconstruct from operating income if available
        oi = raw.get("operating_income") or raw.get("pretax_income")
        if oi is not None and rev_after_provision is not None:
            nie = rev_after_provision - oi
        else:
            nie = None  # discard unreliable value

    # ── Operating income ──────────────────────────────────────────────────
    operating_income = raw.get("operating_income") or raw.get("pretax_income")
    if operating_income is None and rev_after_provision is not None and nie is not None:
        operating_income = rev_after_provision - nie

    # ── Expense sub-components ────────────────────────────────────────────
    expense_detail: Dict[str, float] = {}
    for field_key, label in [
        ("expense_personnel",    "Personnel"),
        ("expense_technology",   "Technology & Equipment"),
        ("expense_occupancy",    "Occupancy"),
        ("expense_professional", "Professional Services"),
        ("expense_marketing",    "Marketing"),
        ("expense_card_services","Card Services"),
        ("expense_other",        "Other Non-Interest Expense"),
    ]:
        v = raw.get(field_key)
        if v and v > 0:
            expense_detail[label] = v

    # Discard sub-components if they exceed NIE (would double-count)
    if expense_detail and nie:
        sub_sum = sum(expense_detail.values())
        if sub_sum > nie * 1.05:
            expense_detail = {}

    result = dict(empty)
    result.update({
        "total_revenue":       total_revenue,
        "operating_income":    operating_income,
        "net_income":          raw.get("net_income"),
        "income_tax":          raw.get("income_tax"),
        # Bank-extra fields
        "nii":                 nii,
        "noninterest_income":  noninterest,
        "provision_credit_loss": provision,
        "noninterest_expense": nie,
        # Map to SegmentData fields for downstream use
        "gross_profit":        rev_after_provision,
        "sga_expense":         nie,                        # NIE ↔ opex analog
        "interest_expense":    provision,                  # provision ↔ interest analog
        "expense_detail":      expense_detail or None,
    })
    return result


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _detect_financial_type(filing_obj) -> str:
    """
    Classify as 'insurance' only when insurance premiums are a significant
    portion of revenue. Many banks (Citi, JPM) carry tiny insurance-related
    XBRL entries that must not trigger the insurance path.
    """
    if filing_obj is None:
        return "bank"
    raw = _parse_bank_concepts(filing_obj)
    premiums = raw.get("insurance_premiums") or 0.0
    benefits = raw.get("insurance_benefits") or 0.0

    # Approximate total revenue for threshold comparison
    net_rev = raw.get("net_revenue_direct") or 0.0
    nii     = raw.get("nii_direct") or 0.0
    nonint  = raw.get("noninterest_income") or 0.0
    total   = net_rev or (nii + nonint) or 1.0  # avoid division by zero

    # Only classify as insurance when premiums are material (>20% of revenue)
    if premiums and premiums > total * 0.20:
        return "insurance"
    # Or when insurance benefits dominate expenses (>30% of revenue)
    if benefits and benefits > total * 0.30:
        return "insurance"
    return "bank"


def _pnl_from_insurance_filing(filing_obj) -> Dict[str, Any]:
    raw = _parse_bank_concepts(filing_obj)
    premiums = raw.get("insurance_premiums") or 0.0
    fee_income = raw.get("noninterest_income") or 0.0
    total_revenue = (premiums + fee_income) if premiums else (fee_income or None)
    benefits = raw.get("insurance_benefits")
    gross_profit: Optional[float] = None
    if total_revenue and benefits:
        gross_profit = total_revenue - abs(benefits)
    return {
        "total_revenue":       total_revenue,
        "gross_profit":        gross_profit,
        "operating_income":    raw.get("operating_income") or raw.get("pretax_income"),
        "net_income":          raw.get("net_income"),
        "income_tax":          raw.get("income_tax"),
        "cogs":                abs(benefits) if benefits else None,
        "sga_expense":         raw.get("noninterest_expense"),
        "rd_expense":          None,
        "interest_expense":    None,
        "currency":            "USD",
        "expense_detail":      None,
    }


def _strip_prefix(concept: str) -> str:
    """Remove namespace prefix: 'us-gaap:ConceptName', 'us-gaap_ConceptName', or 'jpm_Concept'."""
    if ":" in concept:
        return concept.split(":", 1)[-1]
    # edgartools v5 uses underscore: "us-gaap_ConceptName", "jpm_ConceptName"
    # Namespace = all-lowercase (may contain hyphens); concept starts with uppercase
    m = re.match(r'^[a-z][a-z0-9\-]*_(.+)$', concept)
    if m:
        return m.group(1)
    return concept


def _looks_like_period_col(col: str) -> bool:
    s = str(col)
    return bool(
        re.match(r"^\d{4}-\d{2}-\d{2}", s)   # also matches "2025-12-31 (FY)"
        or re.match(r"^\d{4}$", s)
        or re.match(r"^FY\d{4}$", s)
        or re.match(r"^\d{4}Q\d$", s)
    )


def _parse_bank_concepts(filing_obj) -> Dict[str, float]:
    """
    Walk the income_statement dataframe and collect all recognised bank concepts.
    Uses only consolidated (non-dimensioned) rows. Returns {field_name: value}.
    """
    result: Dict[str, float] = {}
    if filing_obj is None:
        return result

    try:
        stmt = filing_obj.income_statement
        df = stmt.to_dataframe() if stmt is not None else None
    except Exception:
        return result

    if df is None or df.empty:
        return result

    period_cols = [c for c in df.columns if _looks_like_period_col(c)]
    if not period_cols:
        return result
    latest_col = period_cols[0]

    # Filter to consolidated (non-dimensioned) rows only
    if "dimension" in df.columns:
        totals = df[~df["dimension"].fillna(False)]
    else:
        totals = df

    for _, row in totals.iterrows():
        concept_raw = str(row.get("concept", "") or "")
        concept_short = _strip_prefix(concept_raw)
        field_name = _BANK_CONCEPTS.get(concept_short)
        if not field_name:
            continue
        raw_val = row.get(latest_col)
        try:
            fval = float(raw_val)
        except (TypeError, ValueError):
            continue
        # Keep largest-magnitude value if concept appears in multiple rows
        existing = result.get(field_name)
        if existing is None or abs(fval) > abs(existing):
            result[field_name] = fval

    return result