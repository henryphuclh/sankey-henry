"""Sector handler: banks, insurance, and financial services companies."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
from config import (
    INSURANCE_COGS_MIN_PCT, INSURANCE_BENEFITS_MIN_PCT,
    BANK_NII_MIN_PCT, BANK_PROVISION_MAX_PCT, INSURANCE_CLAIMS_MAX_PCT,
)


# ---------------------------------------------------------------------------
# US GAAP bank concept map  — applies to US_SEC filers (10-K / 10-Q)
# Covers JPM, BAC, WFC, C, GS, MS, AXP, SCHW, BLK, and US insurance companies.
# ---------------------------------------------------------------------------
_BANK_USGAAP: Dict[str, str] = {

    # ── Net revenue (direct single-line — preferred, avoids double-count) ──
    "RevenuesNetOfInterestExpense":                              "net_revenue_direct",
    "BankingRevenues":                                          "net_revenue_direct",
    "NetRevenues":                                              "net_revenue_direct",
    "TotalNetRevenues":                                         "net_revenue_direct",
    "TotalRevenues":                                            "net_revenue_direct",
    "NetRevenue":                                               "net_revenue_direct",  # SCHW
    "TotalNetRevenue":                                          "net_revenue_direct",
    "Revenues":                                                 "net_revenue_direct",  # C, SCHW, AXP
    "SalesRevenueNet":                                          "net_revenue_direct",
    "NetInterestAndNoninterestIncome":                          "net_revenue_direct",
    "TotalBankingRevenues":                                     "net_revenue_direct",

    # ── Net interest income (direct) ──
    "InterestIncomeExpenseNet":                                 "nii_direct",
    "NetInterestIncome":                                        "nii_direct",
    "InterestAndDividendIncomeOperatingNet":                    "nii_direct",
    "InterestIncomeExpenseNetOperating":                        "nii_direct",
    "InterestAndFeeIncomeOperatingAndNonoperating":             "nii_direct",
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
    "FeeAndCommissionIncome":                                   "noninterest_income",
    "ServiceChargesOnDepositAccounts":                          "noninterest_income",
    "TrustFees":                                                "noninterest_income",
    "InvestmentAdvisoryFees":                                   "noninterest_income",
    "RevenueFromContractWithCustomerExcludingAssessedTax":      "noninterest_income",  # AXP, V, MA
    "RevenueFromContractWithCustomerIncludingAssessedTax":      "noninterest_income",

    # ── Provision for credit losses — US GAAP (CECL and pre-CECL) ──
    "ProvisionForLoanLeaseAndOtherLosses":                      "provision_credit_loss",
    "ProvisionForLoanAndLeaseLosses":                           "provision_credit_loss",
    "CreditLossExpenseReversal":                                "provision_credit_loss",
    "ProvisionForCreditLoss":                                   "provision_credit_loss",
    "ProvisionForCreditLosses":                                 "provision_credit_loss",
    "ProvisionForDoubtfulAccounts":                             "provision_credit_loss",
    "ProvisionForCreditLossesOnFinancingReceivables":           "provision_credit_loss",
    "FinancingReceivableCreditLossExpenseReversal":              "provision_credit_loss",
    "AllowanceForCreditLossesOnFinancingReceivablesExpense":     "provision_credit_loss",
    "ProvisionForLoanLossesExpensed":                           "provision_credit_loss",  # AXP
    "FinancingReceivableExcludingAccruedInterestCreditLossExpenseReversal": "provision_credit_loss",
    "ProvisionForCreditLossBenefitsAndClaimsExpenseReversal":   "provision_credit_loss",
    "OffBalanceSheetCreditLossLiabilityCreditLossExpenseReversal": "provision_credit_loss",
    "FinancingReceivableAllowanceForCreditLossesWriteOffs":     "provision_credit_loss",
    "FinancingReceivableExcludingAccruedInterestAndOffBalanceSheetLiabilityCreditLossProvisionReversal": "provision_credit_loss",

    # ── Non-interest expense ──
    "NoninterestExpense":                                       "noninterest_expense",
    "NonInterestExpense":                                       "noninterest_expense",
    "TotalNoninterestExpense":                                  "noninterest_expense",
    "OperatingExpenses":                                        "noninterest_expense",

    # ── Expense sub-components ──
    "LaborAndRelatedExpense":                                   "expense_personnel",
    "CompensationExpenseExcludingCostOfGoodAndServiceSold":     "expense_personnel",
    "EmployeeBenefitsAndShareBasedCompensation":                "expense_personnel",
    "CompensationAndBenefits":                                  "expense_personnel",  # GS
    "SalariesAndEmployeeBenefits":                              "expense_personnel",
    "InformationTechnologyAndDataProcessing":                   "expense_technology",
    "EquipmentExpense":                                         "expense_technology",
    "TechnologyCommunicationsAndEquipmentExpense":              "expense_technology",  # Citi
    "CommunicationsAndTechnology":                              "expense_technology",
    "CommunicationsAndInformationTechnology":                   "expense_technology",  # JPM
    "OccupancyNet":                                             "expense_occupancy",
    "PremisesAndEquipmentExpense":                              "expense_occupancy",
    "OccupancyAndEquipmentExpense":                             "expense_occupancy",
    "ProfessionalAndContractServicesExpense":                   "expense_professional",
    "ProfessionalFees":                                         "expense_professional",
    "BrokerageClearanceExchangeAndDistributionFees":            "expense_professional",  # GS/MS
    "MarketingAndAdvertisingExpense":                           "expense_marketing",
    "MarketingExpense":                                         "expense_marketing",
    "OtherNoninterestExpense":                                  "expense_other",
    "OtherExpenses":                                            "expense_other",
    "BusinessDevelopmentExpense":                               "expense_other",
    "AmortizationOfIntangibleAssets":                           "expense_other",
    "BusinessAcquisitionCostOfAcquiredEntityTransactionCosts":  "expense_other",
    "RestructuringChargesAndRelatedCosts":                      "expense_other",
    "FDICPremiumExpense":                                       "expense_other",
    "LitigationSettlementExpense":                              "expense_other",
    "CardMemberServicesExpense":                                "expense_card_services",  # AXP
    "RewardsExpense":                                           "expense_card_services",

    # ── Operating / pre-tax income — US GAAP ──
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": "pretax_income",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments": "pretax_income",
    "OperatingIncomeLoss":                                      "operating_income",
    "IncomeLossFromContinuingOperations":                       "operating_income",

    # ── Net income / tax — US GAAP ──
    "NetIncomeLoss":                                            "net_income",
    "NetIncomeLossAvailableToCommonStockholdersBasic":          "net_income",
    "ProfitLoss":                                               "net_income",
    "IncomeTaxExpenseBenefit":                                  "income_tax",

    # ── Insurance — US GAAP ──
    "PremiumsEarnedNet":                                        "insurance_premiums",
    "NetPremiumsEarned":                                        "insurance_premiums",
    "PolicyholderBenefitsAndClaimsIncurredNet":                 "insurance_benefits",
    "BenefitsLossesAndExpenses":                                "insurance_benefits",
    "InsuranceLossesAndLossAdjustmentExpenses":                 "insurance_benefits",
}


# ---------------------------------------------------------------------------
# IFRS bank concept map  — applies to INTL_SEC filers (20-F, ifrs-full / IFRS 9)
# ---------------------------------------------------------------------------
_BANK_IFRS: Dict[str, str] = {

    # ── Net revenue — IFRS banks (total operating income) ──
    "Revenue":                                               "net_revenue_direct",  # ifrs-full:Revenue
    "RevenueAndOperatingIncome":                             "net_revenue_direct",  # HSBC custom extension
    "TotalIncome":                                           "net_revenue_direct",
    "TotalOperatingIncome":                                  "net_revenue_direct",
    "NetOperatingIncome":                                    "net_revenue_direct",
    "NetRevenues":                                           "net_revenue_direct",
    "NetInterestAndNoninterestIncome":                       "net_revenue_direct",  # IFRS variant

    # ── Net interest income — IFRS 9 ──
    "InterestRevenueExpense":                                "nii_direct",   # IFRS 9 primary (Santander, HSBC)
    "InterestRevenueExpenseNet":                             "nii_direct",   # RY.TO variant
    "InterestIncomeExpenseNet1":                             "nii_direct",   # TD Bank extension
    "NetInterestIncome":                                     "nii_direct",   # common in IFRS and GAAP
    # Gross components when direct NII tag absent
    "InterestIncome":                                        "interest_income_gross",
    "InterestExpense":                                       "interest_expense_gross",
    "InterestAndSimilarIncome":                              "interest_income_gross",
    "InterestAndSimilarExpense":                             "interest_expense_gross",
    "InterestIncomeOnFinancialAssets":                       "interest_income_gross",
    "InterestExpenseOnFinancialLiabilities":                 "interest_expense_gross",

    # ── Non-interest / fee income — IFRS ──
    "NonInterestIncome":                                     "noninterest_income",   # RY.TO, capital-I variant
    "NonInterestIncome1":                                    "noninterest_income",   # TD Bank extension
    "FeeAndCommissionIncome":                                "noninterest_income",
    "NetFeeAndCommissionIncome":                             "noninterest_income",
    "OtherOperatingIncome":                                  "noninterest_income",
    "NetTradingIncome":                                      "noninterest_income",
    "GainsLossesOnFinancialInstrumentsAtFairValueThroughProfitOrLoss": "noninterest_income",
    "NetGainsOnFinancialInstruments":                        "noninterest_income",
    "DividendIncome":                                        "noninterest_income",

    # ── Provision for credit losses — IFRS 9 ──
    "ImpairmentLossesOnFinancialAssets":                     "provision_credit_loss",  # IFRS 9 primary
    "ImpairmentLossOnFinancialAssets":                       "provision_credit_loss",
    "CreditLossExpenseReversal":                             "provision_credit_loss",
    "AllowanceForCreditLossesExpense":                       "provision_credit_loss",
    "ImpairmentOnFinancialAssets":                           "provision_credit_loss",
    "IncreaseDecreaseInAllowanceAccountForCreditLossesOfFinancialAssets": "provision_credit_loss",  # TD Bank

    # ── Non-interest expense — IFRS ──
    "NonInterestExpense":                                    "noninterest_expense",   # RY.TO capital-I variant
    "NonInterestExpense1":                                   "noninterest_expense",   # TD Bank extension
    "OperatingExpenses":                                     "noninterest_expense",
    "TotalOperatingExpenses":                                "noninterest_expense",
    "AdministrativeExpenses":                                "noninterest_expense",  # IFRS total admin

    # ── Expense sub-components — IFRS ──
    "EmployeeBenefitsExpense":                               "expense_personnel",
    "PersonnelExpense":                                      "expense_personnel",
    "SalariesAndEmployeeBenefits":                           "expense_personnel",
    "DepreciationAmortisationAndImpairmentLoss":             "expense_other",
    "OtherExpenses":                                         "expense_other",

    # ── Operating / pre-tax income — IFRS ──
    "ProfitBeforeTax":                                       "pretax_income",
    "ProfitLossBeforeTax":                                   "pretax_income",
    "ProfitLossBeforeTaxAndEquityInNetIncomeOfInvestmentInAssociates": "pretax_income",  # TD Bank
    "ProfitFromOperations":                                  "operating_income",
    "OperatingProfitLoss":                                   "operating_income",

    # ── Income tax — IFRS ──
    "IncomeTaxes":                                           "income_tax",
    "TaxExpenseIncome":                                      "income_tax",
    "IncomeTaxExpenseBenefit":                               "income_tax",  # some IFRS filers use GAAP tag
    "IncomeTaxExpenseContinuingOperations":                  "income_tax",  # TD Bank, RY.TO

    # ── Net income — IFRS ──
    "ProfitLoss":                                            "net_income",  # ifrs-full primary
    "ProfitAttributableToOwnersOfParent":                    "net_income",
    "NetIncomeLoss":                                         "net_income",  # some IFRS filers use GAAP tag

    # ── Insurance — IFRS 17 ──
    "PremiumsEarned":                                        "insurance_premiums",
    "InsuranceContractRevenue":                              "insurance_premiums",  # IFRS 17
    "PolicyholderBenefitsAndClaimsIncurredNet":              "insurance_benefits",
    "InsuranceBenefitsAndClaimsAndAdjustmentExpenses":       "insurance_benefits",
}


def has_bank_indicators(pnl: Dict[str, Any], total_rev: float) -> bool:
    """Return True if extracted P&L shows bank or insurance income structure."""
    if not total_rev or total_rev <= 0:
        return False
    if pnl.get("bank_raw_signal"):
        return True
    nii        = pnl.get("nii")
    provision  = pnl.get("provision_credit_loss")
    nonint_inc = pnl.get("noninterest_income")
    bank_nii    = pnl.get("bank_nii")
    bank_nonint = pnl.get("bank_nonint")
    cogs = pnl.get("cogs")
    ins_signal = cogs is not None and cogs > 0 and cogs / total_rev > 0.10

    return bool(
        (nii is not None and nii > 0) or
        (provision is not None and provision > 0) or
        (nonint_inc is not None and nonint_inc > 0) or
        (bank_nii is not None and bank_nii > 0) or
        (bank_nonint is not None and bank_nonint > 0) or
        ins_signal
    )


def pnl_from_financial_filing(
    filing_obj,
    data_source: str = "US_SEC",
) -> Dict[str, Any]:
    """Auto-detect bank vs insurance and dispatch to the right handler.

    For INTL_SEC, auto-detects US GAAP vs IFRS from XBRL namespace prefixes.
    """
    effective = _effective_standard(filing_obj, data_source)
    company_type = _detect_financial_type(filing_obj, effective)
    if company_type == "insurance":
        return _pnl_from_insurance_filing(filing_obj, effective)
    return pnl_from_bank_filing(filing_obj, effective)


def pnl_from_bank_filing(
    filing_obj,
    data_source: str = "US_SEC",
) -> Dict[str, Any]:
    """
    Extract bank P&L from a TenK / TenQ / TwentyF filing object.

    Routes to _BANK_USGAAP for US GAAP filers and _BANK_IFRS for IFRS filers.
    For INTL_SEC, auto-detects the actual accounting standard from XBRL namespaces.
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
        "nii": None,
        "noninterest_income": None,
        "provision_credit_loss": None,
        "noninterest_expense": None,
        "expense_detail": None,
        "bank_raw_signal": False,
    }

    if filing_obj is None:
        return empty

    effective   = _effective_standard(filing_obj, data_source)
    concept_map = _BANK_IFRS if effective == "INTL_SEC" else _BANK_USGAAP
    raw = _parse_bank_concepts(filing_obj, concept_map)

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
        provision = abs(provision)

    # ── Revenue after provision (bank's gross-profit analog) ──────────────
    rev_after_provision: Optional[float] = None
    if total_revenue is not None and provision is not None:
        rev_after_provision = total_revenue - provision
    elif total_revenue is not None:
        rev_after_provision = total_revenue

    # ── Non-interest expense ──────────────────────────────────────────────
    nie = raw.get("noninterest_expense")

    if nie is not None and total_revenue and nie > total_revenue * 0.99:
        oi = raw.get("operating_income") or raw.get("pretax_income")
        if oi is not None and rev_after_provision is not None:
            nie = rev_after_provision - oi
        else:
            nie = None

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

    if expense_detail and nie:
        sub_sum = sum(expense_detail.values())
        if sub_sum > nie * 1.05:
            expense_detail = {}

    bank_raw_signal = bool(
        raw.get("net_revenue_direct") or
        raw.get("nii_direct") or
        raw.get("interest_income_gross") or
        raw.get("interest_expense_gross") or
        raw.get("provision_credit_loss") or
        raw.get("noninterest_income")
    )

    result = dict(empty)
    result.update({
        "total_revenue":       total_revenue,
        "operating_income":    operating_income,
        "net_income":          raw.get("net_income"),
        "income_tax":          raw.get("income_tax"),
        "nii":                 nii,
        "noninterest_income":  noninterest,
        "provision_credit_loss": provision,
        "noninterest_expense": nie,
        "gross_profit":        rev_after_provision,
        "sga_expense":         nie,
        "interest_expense":    provision,
        "expense_detail":      expense_detail or None,
        "bank_raw_signal":     bank_raw_signal,
    })
    return result


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _detect_xbrl_standard(filing_obj) -> str:
    """Infer US GAAP vs IFRS from XBRL concept namespace prefixes.

    Some 20-F filers (e.g. ASML, Alibaba) use us-gaap concepts rather than ifrs-full.
    Returns 'US_GAAP' or 'IFRS'.
    """
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


def _effective_standard(filing_obj, data_source: str) -> str:
    """Return the data_source string to use for concept map selection.

    For INTL_SEC: auto-detects the actual XBRL accounting standard and returns
    'US_SEC' when the filing uses us-gaap concepts, or 'INTL_SEC' for IFRS.
    """
    if data_source != "INTL_SEC":
        return data_source
    detected = _detect_xbrl_standard(filing_obj)
    return "US_SEC" if detected == "US_GAAP" else "INTL_SEC"


def _detect_financial_type(filing_obj, data_source: str) -> str:
    """Classify as 'insurance' only when insurance premiums are a significant
    portion of revenue."""
    if filing_obj is None:
        return "bank"
    concept_map = _BANK_IFRS if data_source == "INTL_SEC" else _BANK_USGAAP
    raw = _parse_bank_concepts(filing_obj, concept_map)
    premiums = raw.get("insurance_premiums") or 0.0
    benefits = raw.get("insurance_benefits") or 0.0

    net_rev = raw.get("net_revenue_direct") or 0.0
    nii     = raw.get("nii_direct") or 0.0
    nonint  = raw.get("noninterest_income") or 0.0
    total   = net_rev or (nii + nonint) or 1.0

    if premiums and premiums > total * INSURANCE_COGS_MIN_PCT:
        return "insurance"
    if benefits and benefits > total * INSURANCE_BENEFITS_MIN_PCT:
        return "insurance"
    return "bank"


def _pnl_from_insurance_filing(filing_obj, data_source: str) -> Dict[str, Any]:
    concept_map = _BANK_IFRS if data_source == "INTL_SEC" else _BANK_USGAAP
    raw = _parse_bank_concepts(filing_obj, concept_map)
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
    m = re.match(r'^[a-z][a-z0-9\-]*_(.+)$', concept)
    if m:
        return m.group(1)
    return concept


def _looks_like_period_col(col: str) -> bool:
    s = str(col)
    return bool(
        re.match(r"^\d{4}-\d{2}-\d{2}", s)
        or re.match(r"^\d{4}$", s)
        or re.match(r"^FY\d{4}$", s)
        or re.match(r"^\d{4}Q\d$", s)
    )


def _parse_bank_concepts(
    filing_obj,
    concept_map: Dict[str, str],
) -> Dict[str, float]:
    """Walk the income_statement dataframe and collect all recognised concepts.
    Uses only consolidated (non-dimensioned) rows."""
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

    if "dimension" in df.columns:
        totals = df[~df["dimension"].fillna(False)]
    else:
        totals = df

    for _, row in totals.iterrows():
        concept_raw = str(row.get("concept", "") or "")
        concept_short = _strip_prefix(concept_raw)
        field_name = concept_map.get(concept_short)
        if not field_name:
            continue
        raw_val = row.get(latest_col)
        try:
            fval = float(raw_val)
        except (TypeError, ValueError):
            continue
        existing = result.get(field_name)
        if existing is None or abs(fval) > abs(existing):
            result[field_name] = fval


# ── Yahoo Finance field resolution for bank / insurance ───────────────────────

def pnl_fields_for_yahoo_financial(m, total_revenue: Optional[float]) -> Dict[str, Any]:
    """Resolve bank/insurance-specific fields from a Yahoo Finance income row.

    m: callable(field_name) -> float | None  (already scaled to USD)

    Returns financial-sector P&L slots. Standard fields (rd_expense, net_income,
    income_tax) are excluded so the caller can fill them generically.
    """
    # Insurance: policyholder benefits map to cogs / gross_profit
    insurance_claims = (
        m("Net Policyholder Benefits And Claims") or
        m("Policyholder Benefits And Claims") or
        m("Total Policy Holder Benefits") or
        m("Policy Holder Benefits")
    )
    ins_cogs_slot = None
    ins_gp_slot   = None
    if insurance_claims and total_revenue and 0 < insurance_claims < total_revenue * INSURANCE_CLAIMS_MAX_PCT:
        ins_cogs_slot = insurance_claims
        ins_gp_slot   = total_revenue - insurance_claims

    # Bank: NII / NIE / provision
    bank_provision = (
        m("Provision For Loan Losses") or
        m("Provision For Credit Losses") or
        m("Provision For Doubtful Accounts") or
        m("Credit Loss Provision")
    )
    bank_nii = m("Net Interest Income")
    _nii_is_bank = (
        bank_nii is not None and bank_nii > 0
        and total_revenue and bank_nii > total_revenue * BANK_NII_MIN_PCT
    )
    bank_nonint = m("Non Interest Income") or m("Net Non Interest Income")
    bank_nie = (
        m("Non Interest Expense") or
        m("Total Non Interest Expense") or
        m("Non-Interest Expense") or
        (m("Operating Expense") if _nii_is_bank else None)
    )

    # Infer non-interest income when only NII is explicit
    if _nii_is_bank and bank_nonint is None and total_revenue is not None:
        implied = total_revenue - bank_nii
        if 0 < implied < total_revenue:
            bank_nonint = implied

    # interest_expense slot: prefer provision; fall back to raw IE; else None
    raw_ie = m("Interest Expense")
    if bank_provision is not None and total_revenue and 0 < bank_provision < total_revenue * BANK_PROVISION_MAX_PCT:
        ie_slot = bank_provision
    elif raw_ie is not None and total_revenue and raw_ie < total_revenue * BANK_PROVISION_MAX_PCT:
        ie_slot = raw_ie
    else:
        ie_slot = None

    # sga_expense: prefer NIE over generic SG&A
    sga_raw = m("Selling General And Administration")
    sga_slot = bank_nie if (bank_nie is not None and total_revenue and 0 < bank_nie < total_revenue) else sga_raw

    # operating_income: banks use Pretax Income when Operating Income absent
    raw_op = m("Operating Income")
    bank_pretax = m("Pretax Income") if _nii_is_bank else None
    op_slot = raw_op or bank_pretax or m("Pretax Income") or m("EBIT")

    return {
        "gross_profit":     ins_gp_slot or m("Gross Profit"),
        "cogs":             ins_cogs_slot or m("Cost Of Revenue"),
        "sga_expense":      sga_slot,
        "interest_expense": ie_slot,
        "operating_income": op_slot,
        "bank_nii":         bank_nii if _nii_is_bank else None,
        "bank_nonint":      bank_nonint,
    }
