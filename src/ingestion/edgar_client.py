"""Edgartools-based SEC client.

Uses `edgartools` v5+ to fetch filings, parse 10-K/10-Q/20-F structure and
extract structured financials WITHOUT manual XBRL or HTML parsing.

Key entry points:
    set_identity_from_env()        -- call once at startup
    get_company(ticker)            -- cached edgar.Company
    get_filings(ticker, forms, N)  -- list[FilingRecord]
    get_annual_pnl(ticker)         -- DataFrame of last N years P&L
    get_filing_obj(filing_record)  -- TenK / TenQ parsed object
    get_segment_note_text(obj)     -- short clean text of segment note (~5-20KB)
    pnl_from_filing_obj(obj)       -- dict of P&L metrics in absolute USD
"""
#Trong Python, import nghĩa là mượn công cụ có sẵn.
from __future__ import annotations

import os
import sys
from dataclasses import asdict
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import SEC_USER_AGENT, YEARS_BACK, CACHE_TTL
from src.cache.cache_manager import cache
from src.extraction.models import FilingRecord


_IDENTITY_SET = False #từ đầu mặc định là chưa đăng ksy danh tính với sec, Dấu gạch dưới ở đầu tên, Biến này chỉ nên dùng nội bộ trong file này.

#def dùng để tạo function. Function là một khối code có tên, khi cần thì gọi.
def set_identity_from_env() -> None: #none nghĩa là function này không trả về dữ liệu gì.
    """Register identity with SEC EDGAR (required by edgartools)."""
    global _IDENTITY_SET #Tôi muốn chỉnh sửa biến _IDENTITY_SET ở bên ngoài function. Nếu không có dòng này, Python sẽ nghĩ _IDENTITY_SET là biến riêng bên trong function.
    if _IDENTITY_SET:
        return #return dùng để kết thúc function.
    import edgar
    edgar.set_identity(os.getenv("SEC_USER_AGENT") or SEC_USER_AGENT) #Thử lấy biến môi trường SEC_USER_AGENT, Gửi identity đó cho thư viện edgar.
    _IDENTITY_SET = True #Sau khi set xong, đánh dấu là: Đã set identity rồi.


# ── Company lookup (cached in-process) ────────────────────────────────────────

@lru_cache(maxsize=128) #decorator, Nó giúp function nhớ lại kết quả cũ để lần sau chạy nhanh hơn.
def get_company(ticker: str): #nghĩa là ticker nên là chuỗi.
    """Return a cached edgar.Company object for a ticker."""
    set_identity_from_env() #Trước khi lấy dữ liệu SEC, function đảm bảo đã set identity.
    from edgar import Company
    return Company(ticker) #Tạo đối tượng đại diện cho công ty Apple trên SEC.


# ── Period helper ─────────────────────────────────────────────────────────────

def _period_from_report_date(report_date_str: str, form: str) -> Tuple[str, bool, int, Optional[int]]: #Tuple giống list, nhưng thường dùng để gom nhiều giá trị cố định.
    """Return (period, is_annual, fiscal_year, fiscal_quarter) from an ISO date."""
    is_annual = form in ("10-K", "20-F", "40-F") #Nếu form là 10-K hoặc 20-F thì đây là báo cáo năm, giá trị báo true, báo cáo quý giá trị báo false
    try: #Đây là try / except: Thử chạy đoạn code này. Nếu lỗi thì xử lý ở phần except.
        d  = date.fromisoformat(report_date_str[:10]) #sẽ biến chuỗi thành ngày tháng,lấy 10 ký tự đầu, 
        fy = d.year #Nếu d là ngày 2025-09-27, thì: fy = 2025
        m  = d.month#m=9
        fq = (m - 1) // 3 + 1#(9-1)/3+1=2+1=3. fq là fiscal quarter
    except (ValueError, TypeError):
        return (f"FY{date.today().year}", is_annual, date.today().year, None)

    if is_annual:
        return (f"FY{fy}", True, fy, None) #Nếu là báo cáo năm: ("FY2025", True, 2025, None)
    return (f"{fy}Q{fq}", False, fy, fq) #Nếu là báo cáo quý: ("2025Q3", False, 2025, 3)


# ── Filings list ──────────────────────────────────────────────────────────────

def get_filings( #Lấy danh sách báo cáo của một công ty trong vài năm gần đây.
    ticker:     str, #Mã công ty.
    form_types: List[str], #Danh sách loại báo cáo.["10-K", "10-Q"]
    years_back: int = YEARS_BACK, #Nếu người dùng không truyền years_back, Python sẽ dùng giá trị mặc định YEARS_BACK.
    cik:        Optional[str] = None,  # CIK fallback for tickers edgartools can't resolve by name
) -> List[FilingRecord]:
    """Return FilingRecord list for a ticker covering the last N years."""
    cache_key = f"filings_list_{ticker}_{'_'.join(sorted(form_types))}" #Tạo tên cache
    """form_types = ["10-Q", "10-K"] -> sorted(form_types) sẽ thành: ["10-K", "10-Q"]
    '_'.join(["10-K", "10-Q"]) sẽ thành: '10-K_10-Q'
    cache key có thể là: "filings_list_AAPL_10-K_10-Q"  """
    cached = cache.get("filings", cache_key)
    if cached:
        return [FilingRecord(**r) for r in cached] #r là dictionary,Dấu ** dùng để bung dictionary thành các tham số.

    # Prefer CIK when explicitly provided — avoids edgartools resolving the
    # wrong entity (e.g. BLK ticker → subsidiary CIK, suffixed tickers that
    # edgartools can't resolve at all like "9988.HK" or "TD.TO").
    if cik:
        try:
            company = get_company(cik)
        except Exception:
            try:
                company = get_company(ticker)
            except Exception:
                return []
    else:
        try:
            company = get_company(ticker)
        except Exception:
            return []

    start_date = date.today() - timedelta(days=years_back * 366) #Lấy ngày hôm nay trừ đi số năm cần lấy. 366 vì phòng năm nhuận
    cik = str(company.cik).zfill(10) #CIK là mã định danh công ty trong SEC.
    #zfill(10) nghĩa là thêm số 0 phía trước cho đủ 10 ký tự. "320193".zfill(10) -> "0000320193"

    records: List[FilingRecord] = [] #tạo danh sách rỗng để chứa kết quả.
    for form in form_types: #nghĩa là chạy qua từng loại form. có form 10k và 10q thì sẽ chạy qua cả 2
        try:
            # amendments=False skips 10-K/A and 10-Q/A which often lack full XBRL data
            filings = company.get_filings(form=form, amendments=False)
        except Exception:
            try:
                filings = company.get_filings(form=form)
            except Exception: #Thử lấy danh sách báo cáo theo form. Nếu lỗi thì bỏ qua form này và chạy tiếp.
                continue    #continue nghĩa là bỏ qua vòng hiện tại và sang vòng tiếp theo.
        for f in filings: #Mỗi f là một báo cáo. kiểu cùng là 10k nhưng có 2025, 2024...
            try:
                fd = date.fromisoformat(str(f.filing_date)[:10]) #Lấy ngày nộp báo cáo. Nếu ngày lỗi thì bỏ qua filing đó.
            except Exception:
                continue
            if fd < start_date: #Nếu báo cáo quá cũ thì dừng vòng lặp, startdate mình set là 3 năm trước, fd mà còn trước cả 3 năm thì dừng
                break  # filings list is newest-first, break nghĩa là thoát hẳn khỏi vòng lặp.
            period, is_annual, fy, fq = _period_from_report_date(
                str(f.period_of_report or f.filing_date)[:10], form
            )
            records.append(FilingRecord( #Nó tạo một object FilingRecord, chứa thông tin của một báo cáo.
                ticker           = ticker,
                form_type        = form,
                period           = period,
                filing_date      = str(f.filing_date)[:10],
                accession_number = str(f.accession_no or ""),
                cik              = cik,
                is_annual        = is_annual,
                fiscal_year      = fy,
                fiscal_quarter   = fq,
            ))

    records.sort(key=lambda r: r.filing_date, reverse=True)
    cache.set("filings", cache_key, [asdict(r) for r in records])
    return records


# ── Filing object (TenK / TenQ) ───────────────────────────────────────────────

def get_filing_obj(rec: FilingRecord):
    """Return the parsed filing object (TenK / TenQ / TwentyF)."""
    set_identity_from_env()
    from edgar import get_by_accession_number
    try:
        filing = get_by_accession_number(rec.accession_number)
    except Exception:
        filing = None
    if filing is None:
        return None
    try:
        return filing.obj()
    except Exception:
        return filing


# ── Segment note text ─────────────────────────────────────────────────────────

def get_segment_note_text(filing_obj) -> Optional[str]:
    """
    Return the clean text of the 'Segment Information' note in a TenK / TenQ.
    This is typically 5–20 KB of focused text (vs 500 KB of raw filing).
    Returns None if no segment note is found.
    """
    if filing_obj is None:
        return None
    notes = getattr(filing_obj, "notes", None)
    if not notes:
        return None

    # Keywords that identify segment / line-of-business notes across different banks.
    # BAC uses "Lines of Business" (plural); WFC uses "Operating Segments"; etc.
    _SEGMENT_TERMS = (
        "segment", "business segment", "lines of business",
        "line of business", "reportable segment", "operating segment",
    )

    segment_note = None
    for note in notes:
        label = (getattr(note, "title", "") or str(note) or "").lower()
        if any(term in label for term in _SEGMENT_TERMS):
            segment_note = note
            break

    if segment_note is None:
        return None
    text = getattr(segment_note, "text", None)
    return text if (text and text.strip()) else None


# ── Structured P&L from a single filing ───────────────────────────────────────

# Map from edgartools concept names → our SegmentData field names
_PNL_CONCEPT_MAP = {
    # revenue
    "RevenueFromContractWithCustomerExcludingAssessedTax": "total_revenue",
    "RevenueFromContractWithCustomerIncludingAssessedTax": "total_revenue",
    "Revenues":                                            "total_revenue",
    "SalesRevenueNet":                                     "total_revenue",
    # cost / gross
    "CostOfRevenue":                "cogs",
    "CostOfGoodsSold":              "cogs",
    "CostOfGoodsAndServicesSold":   "cogs",
    "GrossProfit":                  "gross_profit",
    # operating
    "ResearchAndDevelopmentExpense":           "rd_expense",
    "SellingGeneralAndAdministrativeExpense":  "sga_expense",
    "OperatingIncomeLoss":                     "operating_income",
    # non-op / tax / net
    "InterestExpense":        "interest_expense",
    "IncomeTaxExpenseBenefit": "income_tax",
    "NetIncomeLoss":          "net_income",
    "ProfitLoss":             "net_income",  # IFRS
    "GrossProfitLoss":        "gross_profit",  # IFRS
}


import re as _re


def _strip_concept_prefix(c: str) -> str:
    """'us-gaap_RevenueFromContract...' → 'RevenueFromContract...'"""
    s = str(c or "")
    for pref in ("us-gaap_", "ifrs-full_", "usgaap_", "ifrs_"):
        if s.startswith(pref):
            return s[len(pref):]
    if "_" in s:
        return s.split("_", 1)[1]
    return s


def _looks_like_period_col(col: Any) -> bool:
    s = str(col)
    # Typical columns: "2025-09-27 (FY)", "2024-06-30 (Q2)", "FY 2025"
    return bool(_re.search(r"\d{4}", s)) and any(tok in s for tok in ("FY", "Q", "-"))


def pnl_from_filing_obj(filing_obj) -> Dict[str, Optional[float]]:
    """
    Extract P&L metrics from a parsed filing (TenK / TenQ).
    The income-statement DataFrame from edgartools has 'concept' as a column
    (prefixed 'us-gaap_') and one row per concept×dimension. We take only the
    non-breakdown (total) rows and the latest period column.
    """
    empty = {k: None for k in set(_PNL_CONCEPT_MAP.values())}
    empty["currency"] = "USD"
    if filing_obj is None:
        return empty

    try:
        stmt = filing_obj.income_statement
        df = stmt.to_dataframe() if stmt is not None else None
    except Exception:
        return empty
    if df is None or df.empty:
        return empty

    period_cols = [c for c in df.columns if _looks_like_period_col(c)]
    if not period_cols:
        return empty
    latest_col = period_cols[0]   # edgartools orders newest-first

    # Restrict to total (non-dimension / non-breakdown) rows.
    # In edgartools v5.30, both 'dimension' and 'is_breakdown' are boolean flags.
    totals = df
    if "is_breakdown" in totals.columns:
        totals = totals[totals["is_breakdown"].fillna(False) == False]
    if "dimension" in totals.columns:
        totals = totals[totals["dimension"].fillna(False) == False]

    if "concept" not in totals.columns:
        return empty

    result = dict(empty)
    for _, row in totals.iterrows():
        concept_short = _strip_concept_prefix(row["concept"])
        field_name = _PNL_CONCEPT_MAP.get(concept_short)
        if not field_name:
            continue
        raw = row.get(latest_col)
        try:
            fval = float(raw)
        except (TypeError, ValueError):
            continue
        # Prefer the first non-null; keep the value with the largest magnitude
        # if the concept appears multiple times (e.g., parent + breakdown parent row).
        existing = result.get(field_name)
        if existing is None or abs(fval) > abs(existing):
            result[field_name] = fval
    return result


# ── Segment breakdown from XBRL dimensions ────────────────────────────────────

# Priority of dimension axes (higher = prefer as reportable segment)
_SEGMENT_AXIS_PRIORITY = [
    "us-gaap:StatementBusinessSegmentsAxis",   # cleanest ASC 280 reportable segments (MSFT, AMZN)
    "ifrs-full:SegmentsAxis",                  # IFRS IAS 8 reportable segments (TD.TO, RY.TO, NOVO-B.CO)
    "srt:ConsolidationItemsAxis",              # Operating Segments style (AAPL geographic)
    "ifrs-full:SegmentConsolidationItemsAxis", # IFRS consolidation items (RY.TO)
    "srt:ProductOrServiceAxis",                # product breakdown (NVDA, most retail)
    "ifrs-full:ProductsAndServicesAxis",       # IFRS equivalent of ProductOrServiceAxis (AZN, RHHBY)
    "srt:StatementGeographicalAxis",           # geographic fallback
    "ifrs-full:GeographicalAreasAxis",         # IFRS equivalent of StatementGeographicalAxis
]

# Axes that are never business segments — skip entirely
_BLOCKED_AXES = frozenset({
    "dei:LegalEntityAxis",
    "srt:ConsolidatedEntitiesAxis",
    "srt:MajorCustomersAxis",                          # customer concentration, not segments
    "ifrs-full:MajorCustomersAxis",                    # IFRS equivalent
    "ifrs-full:MarketsOfCustomersAxis",                # customer market geography, not operating segments
    "ifrs-full:NatureExpensesAxis",                    # cost nature breakdown, not segments
    "ifrs-full:NewIFRSsAxis",                          # accounting standard implementation
    "ifrs-full:CategoriesOfFinancialAssetsAxis",       # financial instrument classification, not segments
    "ifrs-full:CategoriesOfFinancialLiabilitiesAxis",  # financial instrument classification, not segments
    "ifrs-full:InvestmentsInEquityInstrumentsMeasuredAtFairValueThroughOtherComprehensiveIncomeAxis",
    "ifrs-full:SignificantInvestmentsInAssociatesAxis",
    "ifrs-full:JointVenturesAxis",
    "ifrs-full:CarryingAmountAccumulatedDepreciationAmortisationAndImpairmentAndGrossCarryingAmountAxis",
    "ry:EmployeeBenefitExpenseAxis",
    "us-gaap:RelatedPartyTransactionsByRelatedPartyAxis",
    "us-gaap:EquityMethodInvestmentNonconsolidatedInvesteeAxis",
    "us-gaap:NatureOfExpenseAxis",
    "us-gaap:StatementEquityComponentsAxis",
    "us-gaap:StatementClassOfStockAxis",
    "us-gaap:ChangeInAccountingEstimateByTypeAxis",
    "us-gaap:ValuationAllowanceByDeferredTaxAssetAxis",
    "us-gaap:RetirementPlanTypeAxis",
    "us-gaap:BusinessAcquisitionAxis",
    "us-gaap:LossContingenciesByNatureOfContingencyAxis",
    "us-gaap:ReclassificationOutOfAccumulatedOtherComprehensiveIncomeAxis",
    "us-gaap:DerivativeInstrumentRiskAxis",            # derivative/hedge, not segments
    "srt:CounterpartyNameAxis",                        # counterparty disclosures, not segments
})

_REV_CONCEPT_HINTS = ("Revenue", "Sales", "NetSales")

# Broader hints for financial-sector filings: bank segments may be tagged with
# NoninterestIncome, PremiumsEarned, or fee concepts rather than "Revenue".
_BANK_REV_CONCEPT_HINTS = ("Revenue", "Sales", "NetSales", "Noninterest", "Premium", "Fee", "Interest")

# ── Geo-only detection for axis selection ────────────────────────────────────
# Used to skip purely geographic axes and prefer product/business axes instead.
_PURE_GEO_TERMS = frozenset({
    "americas", "north america", "latin america", "south america",
    "europe", "emea", "middle east", "africa",
    "asia", "asia pacific", "asia-pacific", "apac",
    "rest of asia pacific", "rest of asia",
    "greater china", "china", "japan",
    "u.s.", "non-u.s.", "non-us", "domestic", "international",
    "united states", "outside u.s.", "outside the u.s.",
    "other regions", "rest of world", "worldwide",
})
_GEO_LABEL_SKIP = frozenset({
    "and", "or", "of", "the", "rest", "other", "greater", "excluding",
    "segment", "segments", "region", "regions",
})


def _is_member_name_geo(name: str) -> bool:
    """Return True only when a segment name is a purely geographic label.

    'Americas', 'U.S.', 'Greater China' → True.
    'Walmart U.S.', 'PepsiCo Beverages North America' → False.
    Non-breaking spaces are normalised before comparison.
    """
    n = name.replace("\xa0", " ").lower().strip()
    remaining = n
    for term in sorted(_PURE_GEO_TERMS, key=len, reverse=True):
        remaining = remaining.replace(term, " ")
    words = {w.strip("().,&-'\"/") for w in remaining.split()} - _GEO_LABEL_SKIP - {""}
    return len(words) == 0


def _members_are_geo_only(members: "List[Tuple[str, float]]") -> bool:
    return bool(members) and all(_is_member_name_geo(n) for n, _ in members)


# Suffixes that indicate a label is a revenue-disaggregation item (ASC 606),
# not a reportable business segment (ASC 280).
_REVENUE_DISAGG_SUFFIXES = (
    " service", " services", " revenue", " revenues", " sales",
    " equipment", " broadband", " voice and data",
)
_REVENUE_DISAGG_SUBSTRINGS = (
    "property plant and equipment", "capitalized cost",
)


def _is_revenue_disaggregation(members: "List[Tuple[str, float]]") -> bool:
    """True when a majority of member names look like ASC 606 revenue line items."""
    if not members:
        return False
    revenue_like = 0
    for name, _ in members:
        n_low = name.lower()
        if (any(n_low.endswith(sfx) for sfx in _REVENUE_DISAGG_SUFFIXES)
                or any(sub in n_low for sub in _REVENUE_DISAGG_SUBSTRINGS)):
            revenue_like += 1
    return revenue_like >= len(members) * 0.6


def segments_from_xbrl_dimensions(
    filing_obj,
    total_revenue: Optional[float] = None,
    is_financial: bool = False,
    ticker: str = "",
) -> List[Any]:
    """Extract segment breakdown directly from XBRL dimensions (no LLM).

    Returns a list of SegmentValue. Empty list if no usable axis is found — caller
    can then fall back to LLM.
    """
    from src.extraction.models import SegmentValue  # local to avoid circular

    if filing_obj is None:
        return []
    try:
        stmt = filing_obj.income_statement
        df = stmt.to_dataframe() if stmt is not None else None
    except Exception:
        return []
    if df is None or df.empty or "dimension_axis" not in df.columns:
        return []

    period_cols = [c for c in df.columns if _looks_like_period_col(c)]
    if not period_cols:
        return []
    latest_col = period_cols[0]

    # Only revenue-like concepts and only dimensioned rows.
    # Financial companies use bank-specific income concepts for segment reporting.
    hints = _BANK_REV_CONCEPT_HINTS if is_financial else _REV_CONCEPT_HINTS
    concept_series = df["concept"].fillna("").astype(str)
    is_rev = concept_series.apply(
        lambda c: any(h.lower() in _strip_concept_prefix(c).lower() for h in hints)
    )
    rev_df = df[is_rev & (df["dimension"].fillna(False) == True)]
    if rev_df.empty:
        return []

    # Try axes in priority order; return first one that produces a clean set
    axis_values = rev_df["dimension_axis"].fillna("").astype(str)
    available_axes = [a for a in axis_values.unique() if a not in _BLOCKED_AXES]
    ordered_axes = [a for a in _SEGMENT_AXIS_PRIORITY if a in available_axes]
    # Also include any unknown axes as lowest priority (skip blocked ones)
    ordered_axes += [a for a in available_axes if a and a not in ordered_axes]

    geo_fallback: "Optional[List[Any]]" = None  # best geo-only result (last resort)

    for axis in ordered_axes:
        sub = rev_df[axis_values == axis]
        members = _clean_axis_members(sub, latest_col, total_revenue)
        if len(members) < 2:
            continue
        period = str(latest_col)
        candidate = [
            SegmentValue(
                segment_name = name,
                value        = float(val),
                unit         = "USD",
                period       = period,
                concept      = "xbrl_dimension",
                is_annual    = True,
            )
            for name, val in members
        ]
        # StatementGeographicalAxis / GeographicalAreasAxis are geo by definition
        _GEO_AXES = {"srt:StatementGeographicalAxis", "ifrs-full:GeographicalAreasAxis"}
        _is_geo = _members_are_geo_only(members) or axis in _GEO_AXES
        if _is_geo:
            # Pure geographic axis — save as fallback and keep trying for business segments
            if geo_fallback is None:
                geo_fallback = candidate
            continue

        # ProductOrServiceAxis with only revenue-disaggregation-style names (ASC 606,
        # not ASC 280) should be skipped — e.g. AT&T's "Wireless Service", "Equipment".
        _PROD_AXIS = "srt:ProductOrServiceAxis"
        if axis == _PROD_AXIS and _is_revenue_disaggregation(members):
            continue

        # Mixed axis: some members are geo, some are not (e.g. AMZN's
        # StatementBusinessSegmentsAxis = North America + International + AWS).
        # If ProductOrServiceAxis exists with MORE geo-free members, prefer it.
        if (
            axis != _PROD_AXIS
            and _PROD_AXIS in available_axes
            and any(_is_member_name_geo(n) for n, _ in members)
        ):
            sub_prod = rev_df[axis_values == _PROD_AXIS]
            prod_members = _clean_axis_members(sub_prod, latest_col, total_revenue)
            # Count members that contain no geo terms at all
            prod_clean = [
                m for m in prod_members
                if not any(
                    term in m[0].replace("\xa0", " ").lower()
                    for term in _PURE_GEO_TERMS
                )
            ]
            if len(prod_clean) > len(members):
                return [
                    SegmentValue(
                        segment_name = name,
                        value        = float(val),
                        unit         = "USD",
                        period       = period,
                        concept      = "xbrl_dimension",
                        is_annual    = True,
                    )
                    for name, val in prod_members
                ]

        return candidate

    # Fallback: some filers (e.g. MRK, RY.TO) tag segment totals in dimension_label rather
    # than as the primary dimension_axis.  Look for rows where dimension_label contains
    # "StatementBusinessSegmentsAxis: <SegmentName>" but NO ProductOrServiceAxis /
    # GeographicalAxis — those are the segment-level totals without sub-disaggregation.
    if "dimension_label" in df.columns:
        try:
            # Both US-GAAP and IFRS segment axis names
            seg_axis_candidates = ("StatementBusinessSegmentsAxis", "ifrs-full:SegmentsAxis")
            seg_axis_str = next(
                (s for s in seg_axis_candidates
                 if df["dimension_label"].fillna("").astype(str).str.contains(s, na=False).any()),
                None,
            )
            if seg_axis_str is None:
                raise ValueError("no segment axis in dimension_label")
            skip_axes = ("ProductOrServiceAxis", "ProductsAndServicesAxis",
                         "GeographicalAxis", "CounterpartyName",
                         "DerivativeInstrument", "AssetAcquisition",
                         "CategoriesOfFinancial", "InvestmentsInEquity",
                         "SignificantInvestments", "JointVentures",
                         "CarryingAmount", "EmployeeBenefit")
            lbl_col = df["dimension_label"].fillna("").astype(str)
            is_rev = df["concept"].fillna("").astype(str).apply(
                lambda c: any(h.lower() in _strip_concept_prefix(c).lower() for h in hints)
            )
            candidate_mask = (
                lbl_col.str.contains(seg_axis_str, na=False)
                & ~lbl_col.apply(lambda l: any(sk in l for sk in skip_axes))
                & is_rev
            )
            cand_df = df[candidate_mask]
            seg_members: Dict[str, float] = {}
            for _, row in cand_df.iterrows():
                try:
                    val = float(row.get(latest_col, None))
                except (TypeError, ValueError):
                    continue
                if val <= 0:
                    continue
                # Extract segment name: "..., StatementBusinessSegmentsAxis: <Name>"
                lbl = str(row.get("dimension_label", ""))
                idx = lbl.find(seg_axis_str + ":")
                if idx < 0:
                    continue
                seg_name = lbl[idx + len(seg_axis_str) + 1:].strip().split(",")[0].strip()
                if not seg_name:
                    continue
                if seg_name not in seg_members or val > seg_members[seg_name]:
                    seg_members[seg_name] = val
            if len(seg_members) >= 2:
                members_list = list(seg_members.items())
                # Skip subtotal-drop when total_revenue appears to be a partial figure
                # (e.g. NII-only for IFRS banks where segment revenue = NII + NonInterestIncome).
                # Heuristic: if segment sum > 1.5× total_revenue, the reference is likely
                # capturing only a subset of income → disable filtering.
                _ref_rev = total_revenue
                if _ref_rev and sum(v for _, v in members_list) > _ref_rev * 1.5:
                    _ref_rev = None
                members_list = _clean_axis_members_from_list(members_list, _ref_rev)
                if len(members_list) >= 2:
                    return [
                        SegmentValue(
                            segment_name = name,
                            value        = float(val),
                            unit         = "USD",
                            period       = str(latest_col),
                            concept      = "xbrl_dimension",
                            is_annual    = True,
                        )
                        for name, val in members_list
                    ]
        except Exception:
            pass

    if geo_fallback:
        # Tag these so extraction_router knows they came from a geographic axis
        for sv in geo_fallback:
            sv.concept = "xbrl_geo_axis"
        return geo_fallback

    return []


def _clean_axis_members_from_list(
    members: List[Tuple[str, float]], total_revenue: Optional[float],
) -> List[Tuple[str, float]]:
    """Apply subtotal-drop heuristic to an already-built (name, value) list."""
    import math as _math
    members = [(n, v) for n, v in members if v > 0 and not _math.isnan(v)]
    if not members:
        return []
    if total_revenue and total_revenue > 0:
        tol_hi = total_revenue * 1.15
        tol_lo = total_revenue * 0.85
        guard = 0
        while len(members) > 2 and guard < 5:
            s = sum(v for _, v in members)
            if tol_lo <= s <= tol_hi:
                break
            if s > tol_hi:
                members.sort(key=lambda t: t[1], reverse=True)
                members.pop(0)
                guard += 1
            else:
                return []
    return members


def _clean_axis_members(
    sub_df, latest_col: Any, total_revenue: Optional[float],
) -> List[Tuple[str, float]]:
    """Pull (name, value) pairs from an axis slice, dropping subtotals.

    Heuristic: if sum of members > total_revenue × 1.1, repeatedly drop the
    largest member until sum ≈ total_revenue (within ±15%) or we'd delete the
    only remaining member.
    """
    import math as _math
    members: List[Tuple[str, float]] = []
    for _, row in sub_df.iterrows():
        raw = row.get(latest_col)
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if v <= 0 or _math.isnan(v):
            continue
        name = _segment_display_name(row)
        if not name:
            continue
        members.append((name, v))

    if not members:
        return []

    # De-dup by name (keep largest)
    dedup: Dict[str, float] = {}
    for n, v in members:
        if n not in dedup or v > dedup[n]:
            dedup[n] = v
    members = list(dedup.items())

    # Remove sub-segments: if member X's name contains another member Y's shorter
    # name, X is likely a sub-component of Y (e.g. "IS—Underwriting - IS" ⊂ "IS").
    # Only filter when enough members remain after removal.
    if len(members) >= 3:
        member_names = [n for n, _ in members]
        filtered = [
            (n, v) for n, v in members
            if not any(
                other != n and len(other) < len(n) and other.lower() in n.lower()
                for other in member_names
            )
        ]
        if len(filtered) >= 2:
            members = filtered

    if total_revenue and total_revenue > 0:
        tol_hi = total_revenue * 1.15
        tol_lo = total_revenue * 0.85
        # Drop obvious subtotals (largest) until within tolerance
        guard = 0
        while len(members) > 2 and guard < 5:
            s = sum(v for _, v in members)
            if tol_lo <= s <= tol_hi:
                break
            if s > tol_hi:
                # remove largest
                members.sort(key=lambda t: t[1], reverse=True)
                members.pop(0)
                guard += 1
            else:
                # sum fell below tol_lo after a drop → intercompany inflation,
                # axis data is unusable for segment extraction
                return []
    return members


# Labels to skip — pure aggregates / generic / income-concept labels, not reportable segments
_SKIP_SEGMENT_LABELS = {
    "products", "product", "service and other", "net product sales", "net service sales",
    "sales of products", "sales of services",
    "sales and other operating revenue", "sales and other operating revenues",
    "income from equity affiliates",
    "other income", "other income and expense", "other revenue",
    "operating segments", "corporate and other", "total",
    # Generic revenue-type labels that are disaggregation items, not business segments
    "service", "equipment", "services revenues", "product revenues",
    "wireless", "wireline",
}

# Prefixes that block a label even when followed by a suffix (XOM-style compound labels
# like "Sales and other operating revenue - Non-U.S." or
# "Income from equity affiliates - Non-U.S. - Upstream").
_SKIP_SEGMENT_PREFIXES = frozenset({
    "sales and other operating revenue",
    "income from equity affiliates",
    "sales of products",
    "sales of services",
    "net product sales",
    "net service sales",
    "other income",
    # GEV-style aggregation labels: "Operating segments, inclusive of intersegment sales"
    "operating segments,",
})


# IFRS SegmentConsolidationItemsAxis carries both:
#   a) "Constant currency - {SegName}" rows: per-segment revenue at constant FX
#      → extract segment name from the " - {SegName}" suffix
#   b) "Operating segments excluding intersegment elimination - {SegName}" rows:
#      pre-elimination subtotals → inflated values, skip entirely
#   c) "Inter-segment - {SegName}" rows: intercompany adjustment → skip entirely
# Set (a) member labels that we should parse for a segment suffix:
_CONSOLIDATION_EXTRACT_LABELS = frozenset({"constant currency"})
# Set (b/c) member labels whose rows must be skipped entirely (no segment extracted):
_CONSOLIDATION_SKIP_LABELS = frozenset({
    "operating segments excluding intersegment elimination",
    "inter-segment",
    "currency translation",
})


def _segment_display_name(row) -> str:
    """Build a clean segment name from a row. Prefer member_label, fall back to label."""
    # Handle IFRS SegmentConsolidationItemsAxis compound labels first.
    # These rows have a consolidation-dimension member (e.g. "Constant currency") but
    # the real segment name is embedded in the label as "Constant currency - Hong Kong".
    member_lbl_raw = str(row.get("dimension_member_label") or "").strip()
    member_lbl_low = _re.sub(r'\s*\[[Mm]ember\]$', '', member_lbl_raw).strip().lower()

    if member_lbl_low in _CONSOLIDATION_SKIP_LABELS:
        return ""  # pre-elimination subtotals and inter-segment rows → always skip

    if member_lbl_low in _CONSOLIDATION_EXTRACT_LABELS:
        # "Constant currency - Hong Kong" → segment name is "Hong Kong"
        lbl_raw = str(row.get("label") or "").strip()
        if " - " in lbl_raw:
            prefix_part, seg_part = lbl_raw.split(" - ", 1)
            if prefix_part.strip().lower() == member_lbl_low:
                # Valid compound: use extracted segment name
                candidates = [seg_part.strip()]
            else:
                return ""  # unexpected compound format → skip
        else:
            return ""  # rollup total row (e.g. label = "Constant currency") → skip
    else:
        candidates = [
            row.get("dimension_member_label"),
            row.get("label"),
        ]

    for c in candidates:
        s = str(c or "").strip()
        if not s:
            continue
        # Strip XBRL "[member]" / "[Member]" suffix (IFRS 20-F filers: NVS, RHHBY, ASML)
        s = _re.sub(r'\s*\[[Mm]ember\]$', '', s).strip()
        # Strip boilerplate segment-reporting prefixes
        for pref in ("Operating segments - ", "Operating Segments - ", "Reportable segments - "):
            if s.startswith(pref):
                s = s[len(pref):]
        s_low = s.lower()
        if s_low in _SKIP_SEGMENT_LABELS:
            continue
        # Skip inter-segment / consolidation adjustment labels (with or without hyphen)
        if s_low.startswith("inter-segment") or s_low.startswith("intersegment"):
            continue
        # Skip "Operating segments excluding..." (pre-intercompany-elimination subtotals)
        if s_low.startswith("operating segments excluding"):
            continue
        # Skip compound labels that start with an income-concept phrase (XOM pattern)
        if any(s_low.startswith(pref) for pref in _SKIP_SEGMENT_PREFIXES):
            continue
        # Skip XBRL reconciling-item / rollup / elimination labels
        if (s_low.startswith("segment reporting")
                or s_low.startswith("consolidation, elim")
                or s_low.startswith("total ")          # "Total segment profits", "Total revenues"
                or "reportable segment, aggregation" in s_low
                or "segment profits" in s_low          # MRK ConsolidationItemsAxis rollup
                or "segment losses" in s_low):
            continue
        # Skip ASC 606 revenue timing categories (SPGI-style: "transferred at a point in time")
        if "transferred at a point in time" in s_low or "transferred over time" in s_low:
            continue
        return s[:80]
    return ""


def products_services_from_income_statement(
    filing_obj,
    total_revenue: Optional[float] = None,
) -> List[Any]:
    """Extract Products / Services split from the top-level income statement.

    AAPL and similar companies report this split as top-level line items
    (not as XBRL dimensions), so segments_from_xbrl_dimensions misses them.
    Returns a list of SegmentValue, or [] if not found.
    """
    from src.extraction.models import SegmentValue

    if filing_obj is None:
        return []
    try:
        stmt = filing_obj.income_statement
        df   = stmt.to_dataframe() if stmt is not None else None
    except Exception:
        return []
    if df is None or df.empty:
        return []

    period_cols = [c for c in df.columns if _looks_like_period_col(c)]
    if not period_cols:
        return []
    latest_col = period_cols[0]

    # Look for rows labelled "Products" and "Services" (non-dimensional)
    _PRODUCT_LABELS  = {"products", "product", "net product sales", "hardware"}
    _SERVICE_LABELS  = {"services", "service", "net service sales", "software and services"}

    label_col = "label" if "label" in df.columns else None
    if label_col is None:
        return []

    result = []
    is_dimensional = df.get("dimension", None)
    for _, row in df.iterrows():
        # Skip dimensioned rows (those belong to segment axes)
        if is_dimensional is not None and row.get("dimension"):
            continue
        label = str(row.get(label_col) or "").strip().lower()
        matched = None
        if label in _PRODUCT_LABELS:
            matched = "Products"
        elif label in _SERVICE_LABELS:
            matched = "Services"
        if matched is None:
            continue
        raw = row.get(latest_col)
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if v <= 0:
            continue
        result.append(SegmentValue(
            segment_name = matched,
            value        = v,
            unit         = "USD",
            period       = str(latest_col),
            concept      = "income_statement_line",
            is_annual    = True,
        ))

    # Only return if we got both Products and Services
    names = {s.segment_name for s in result}
    if "Products" in names and "Services" in names:
        return result
    return []


def product_segments_from_mda(
    filing_obj,
    period: str = "",
    is_annual: bool = True,
) -> List[Any]:
    """Parse product revenue table from MD&A text (e.g. AAPL iPhone/Mac/iPad).

    Finds the revenue table where iPhone appears at start of line followed by
    a dollar amount, e.g.:
        iPhone$209,586 4 %$201,183...
        Mac33,708 12 %...
    Returns [] if pattern not found or fewer than 3 products found.
    """
    import re as _re2
    from src.extraction.models import SegmentValue

    if filing_obj is None:
        return []
    mda = getattr(filing_obj, 'management_discussion', None)
    if mda is None:
        return []
    mda_text = getattr(mda, 'text', None) or str(mda)
    if not mda_text:
        return []

    mda_text = mda_text.replace('\xa0', ' ')

    table_match = _re2.search(r'iPhone\s*(?:\(\d+\)\s*)?\$\s*\d{2,3},\d{3}', mda_text)
    if not table_match:
        table_match = _re2.search(r'iPhone\s+\d{2,3},\d{3}', mda_text)
    if not table_match:
        return []

    block_start = max(0, table_match.start() - 10)
    block = mda_text[block_start:block_start + 800]
    block_norm = _re2.sub(r'\s*\(\d+\)\s*', ' ', block)
    block_norm = _re2.sub(r'\n+', ' ', block_norm)

    chunks = _re2.split(r'  +', block_norm)

    _SKIP_WORDS = {'total', 'net sales', 'total net', '2025', '2024', '2023', '2022', '2021', 'change'}

    results = []
    for chunk in chunks:
        chunk = chunk.strip()
        nm = _re2.search(r'(\d{2,3},\d{3})', chunk)
        if not nm:
            continue
        try:
            val = float(nm.group(1).replace(',', '')) * 1_000_000
        except ValueError:
            continue
        if val < 1_000_000_000:
            continue
        name = chunk[:nm.start()].rstrip('$ ').strip()
        name = _re2.sub(r'\s*\(\d+\)\s*$', '', name).strip()
        name = _re2.sub(r'^[\w]*\d{4}\s*', '', name).strip()
        # Strip "Category: " or similar prefix before the actual name
        if ':' in name:
            name = name.split(':')[-1].strip()
        if not name or any(s in name.lower() for s in _SKIP_WORDS):
            continue
        results.append(SegmentValue(
            segment_name = name,
            value        = val,
            unit         = "USD",
            period       = period,
            concept      = "mda_table",
            is_annual    = is_annual,
        ))

    if len(results) < 3:
        return []
    return results


# ── Companyfacts (for TTM / cross-check) ──────────────────────────────────────

def get_company_facts(ticker: str) -> Optional[Dict]:
    """Return raw companyfacts dict via edgar.Company.facts. Cached 7 days."""
    cached = cache.get("xbrl", f"facts_{ticker}")
    if cached:
        return cached
    try:
        company = get_company(ticker)
        facts   = company.facts
        # .facts is an EntityFacts object — convert to dict by attribute
        # edgartools usually exposes .to_dict() or similar
        if hasattr(facts, "to_dict"):
            d = facts.to_dict()
        else:
            d = None
        if d:
            cache.set("xbrl", f"facts_{ticker}", d)
        return d
    except Exception:
        return None