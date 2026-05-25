"""Build Sankey node/link data from SegmentData.

Standard flow (7 layers):
  Segments → Total Revenue → Gross Profit ←→ COGS
  → R&D / SG&A / Other OpEx → Operating Income
  → Interest & Other / Income Tax → Net Income

Bank flow (6 layers):
  Segments → Net Revenue → Provision for Credit Losses
  → Revenue after Provision → Personnel / Technology / … / Other NIE
  → Pre-tax Income → Income Tax → Net Income
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import SANKEY_COLORS, SANKEY_MIN_LINK_PCT, INSURANCE_COGS_MIN_PCT, BANK_PROVISION_MAX_PCT, BANK_NII_MIN_PCT
from src.extraction.models import SegmentData, SegmentValue


@dataclass
class SankeyData:
    node_labels:  List[str]
    node_colors:  List[str]
    node_x:       List[float]
    node_y:       List[float]
    link_sources: List[int]
    link_targets: List[int]
    link_values:  List[float]
    link_colors:  List[str]
    link_labels:  List[str]
    period:       str
    ticker:       str
    currency:     str = "USD"


# Geographic keywords used to pick product segments over geo segments.
_GEO_KEYWORDS = {
    "americas", "europe", "greater china", "japan", "asia pacific",
    "asia-pacific", "rest of asia", "north america", "latin america",
    "middle east", "africa", "emea", "apac",
    "u.s.", "non-u.s.", "non-us", "domestic", "international",
    "united states", "outside u.s.", "rest of world",
}

# Financial sector identifiers (sector string returned by data_source_checker)
_FINANCIAL_SECTORS = {"financial", "financials", "banking", "insurance"}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_sankey(sd: SegmentData, sector: str = "") -> Optional[SankeyData]:
    """
    Convert SegmentData into SankeyData.

    Parameters
    ----------
    sd     : SegmentData — source data for one period.
    sector : str — sector label from TickerInfo (e.g. "financial").
                   Pass empty string for standard companies.
    """
    if sd is None:
        return None

    total_rev = sd.total_revenue
    if not total_rev or total_rev == 0:
        if sd.segments:
            total_rev = sum(s.value for s in sd.segments if s.value)
        if not total_rev:
            return None

    import copy
    sd = copy.copy(sd)
    sd.segments = _pick_best_segments(sd.segments, total_rev)

    is_financial = sector.lower() in _FINANCIAL_SECTORS
    is_yahoo_source = getattr(sd, "extraction_method", "") == "yahoo+llm"

    if is_financial:
        if is_yahoo_source:
            return _build_yahoo_financial_sankey(sd, total_rev)
        return _build_bank_sankey(sd, total_rev)
    return _build_standard_sankey(sd, total_rev)


# ---------------------------------------------------------------------------
# Standard (non-financial) Sankey
# ---------------------------------------------------------------------------

def _build_standard_sankey(sd: SegmentData, total_rev: float) -> Optional[SankeyData]:
    nodes, colors, xs, ys = [], [], [], []
    link_src, link_tgt, link_val, link_col, link_lbl = [], [], [], [], []

    def add_node(label, color, x, y):
        nodes.append(label); colors.append(color); xs.append(x); ys.append(y)
        return len(nodes) - 1

    def add_link(src, tgt, val, color, label=""):
        if val is None or val <= 0:
            return
        min_v = max(total_rev * SANKEY_MIN_LINK_PCT, abs(val) * 0.001)
        link_src.append(src); link_tgt.append(tgt)
        link_val.append(max(abs(val), min_v))
        link_col.append(color); link_lbl.append(label)

    fmt   = _make_fmt(total_rev)
    rich  = lambda lbl, v: f"{lbl}<br>{fmt(v)}{_pct(v, total_rev)}" if v else lbl

    # Layer 0+1: Revenue segments → Total Revenue.
    # Skip segment layer when no segment data is available.
    has_segments = bool(sd.segments)
    has_hierarchy = bool(getattr(sd, "segment_hierarchy", None) and getattr(sd, "sub_segments", None))
    rev_x = 0.25 if has_hierarchy else (0.2 if has_segments else 0.05)
    rev_idx = add_node(rich("Total Revenue", total_rev), SANKEY_COLORS["total_revenue"], rev_x, 0.5)
    if has_hierarchy:
        _add_two_layer_segment_nodes(
            sd.segments, sd.sub_segments, sd.segment_hierarchy,
            total_rev, rev_idx, add_node, add_link, rich,
        )
    elif has_segments:
        _add_segment_nodes(sd.segments, total_rev, rev_idx, add_node, add_link, rich)

    # Layer 2: Gross Profit ←→ COGS
    # Expense fields (cogs, rd, sga) are stored as positive magnitudes; guard with
    # abs() in case a legacy segment file still contains negative XBRL values.
    gp   = sd.gross_profit
    cogs = abs(sd.cogs)  if sd.cogs  is not None else None
    op   = sd.operating_income
    rd   = abs(sd.rd_expense)  if sd.rd_expense  is not None else None
    sga  = abs(sd.sga_expense) if sd.sga_expense is not None else None
    if gp is not None and cogs is None:   cogs = total_rev - gp
    elif cogs is not None and gp is None: gp   = total_rev - cogs
    elif gp is None and cogs is None and op is not None:
        opex = (rd or 0) + (sga or 0)
        gp   = op + opex
        cogs = total_rev - gp

    # When gp is known but op is not, infer op from below (ni + tax + ie).
    # This closes the visual gap in conglomerates (BRK-B) and other companies
    # where SEC/Yahoo provides COGS + NI but no explicit operating income line.
    if op is None and gp is not None:
        _ni = sd.net_income; _tax = sd.income_tax; _ie = sd.interest_expense
        if _ni is not None and _tax is not None and abs(_tax) > 0:
            _inferred = abs(_ni) + abs(_tax) + abs(_ie or 0)
            if 0 < _inferred < gp:
                op = _inferred if (_ni >= 0) else -_inferred

    gp_idx = None
    if gp and gp > 0:
        gp_idx = add_node(rich("Gross Profit", gp), SANKEY_COLORS["gross_profit"], 0.4, 0.35)
        add_link(rev_idx, gp_idx, gp, "rgba(44,160,44,0.4)", f"Gross Profit{_pct(gp, total_rev)}")
    if cogs and cogs > 0:
        co_idx = add_node(rich("Cost of Revenue", cogs), SANKEY_COLORS["cogs"], 0.4, 0.75)
        add_link(rev_idx, co_idx, cogs, "rgba(214,39,40,0.4)", f"COGS{_pct(cogs, total_rev)}")

    # Layer 3: OpEx
    gp_src = gp_idx if gp_idx is not None else rev_idx
    opex_total = (rd or 0) + (sga or 0)
    if rd and rd > 0:
        rd_idx = add_node(rich("R&D", rd), SANKEY_COLORS["rd"], 0.6, 0.2)
        add_link(gp_src, rd_idx, rd, "rgba(255,127,14,0.4)", f"R&D{_pct(rd, total_rev)}")
    if sga and sga > 0:
        sg_idx = add_node(rich("SG&A", sga), SANKEY_COLORS["sga"], 0.6, 0.4)
        add_link(gp_src, sg_idx, sga, "rgba(255,127,14,0.4)", f"SG&A{_pct(sga, total_rev)}")
    if gp and op:
        other_opex = gp - opex_total - op
        if other_opex > total_rev * 0.01:
            oo_idx = add_node(rich("Other OpEx", other_opex), SANKEY_COLORS["other_opex"], 0.6, 0.6)
            add_link(gp_src, oo_idx, other_opex, "rgba(255,187,120,0.4)", "Other Operating Expenses")

    # Layer 4: Operating Income
    oi_idx = None
    if op is not None:
        color = SANKEY_COLORS["operating_income"] if op > 0 else SANKEY_COLORS["net_loss"]
        label = "Operating Income" if op > 0 else "Operating Loss"
        oi_idx = add_node(rich(label, op), color, 0.75, 0.5)
        add_link(gp_src, oi_idx, abs(op),
                 "rgba(44,160,44,0.4)" if op > 0 else "rgba(214,39,40,0.4)",
                 f"Operating Income{_pct(op, total_rev)}")

    # Layer 5: Below-the-line — with balance enforcement so op node stays balanced.
    final_src = oi_idx if oi_idx is not None else gp_src
    ie  = abs(sd.interest_expense) if sd.interest_expense is not None else None
    tax = abs(sd.income_tax)       if sd.income_tax       is not None else None
    ni  = sd.net_income

    # Implied tax when tax is absent but op and ni are known
    if (tax is None or tax <= 0) and op is not None and ni is not None and op > 0 and ni > 0 and op > ni:
        _implied_tax = op - abs(ni) - (ie or 0)
        if _implied_tax > total_rev * 0.005:
            tax = _implied_tax

    ie_w  = ie  if ie  else 0.0
    tax_w = tax if tax else 0.0
    ni_w  = abs(ni)  if ni is not None    else 0.0
    op_abs = abs(op) if op is not None and op > 0 else 0.0

    # Simulate the min_v floor that add_link applies, to detect inflation-caused overflow.
    _min_v = total_rev * SANKEY_MIN_LINK_PCT
    ie_inf  = _min_v if (0 < ie_w < _min_v) else ie_w
    total_inf = ie_inf + tax_w + ni_w

    # Scale factor for tax + ni: when ie is inflated by min_v, fit the rest in the
    # remaining budget so the op node stays visually balanced.
    down_scale = 1.0
    tn_scale   = 1.0
    if op_abs > 0 and total_inf > 0:
        if total_inf > op_abs * 1.005:
            if ie_inf > ie_w:
                # ie inflation causes overflow — keep ie as-is, scale tax+ni to fit
                avail = max(0.0, op_abs - ie_inf)
                tn_scale = min(1.0, avail / (tax_w + ni_w)) if (tax_w + ni_w) > 0 else 1.0
            else:
                # raw sum exceeds op — scale everything down uniformly
                down_scale = op_abs / total_inf

    if ie_w > 0:
        i_idx = add_node(rich("Interest & Other", ie), SANKEY_COLORS["interest"], 0.87, 0.35)
        add_link(final_src, i_idx, ie_w * down_scale, "rgba(127,127,127,0.4)", "Interest & Other")
    if tax_w > 0:
        t_idx = add_node(rich("Income Tax", tax), SANKEY_COLORS["tax"], 0.87, 0.65)
        add_link(final_src, t_idx, tax_w * down_scale * tn_scale, "rgba(127,127,127,0.4)", "Income Tax")

    # Fill gap when op > ie + tax + ni (minority interest, other adjustments)
    if op_abs > 0 and (tax_w + ni_w + ie_w) > 0 and down_scale == 1.0 and tn_scale == 1.0:
        gap = op_abs - (ie_w + tax_w + ni_w)
        if gap > total_rev * 0.005:
            g_idx = add_node(rich("Other", gap), SANKEY_COLORS["other_opex"], 0.9, 0.4)
            add_link(final_src, g_idx, gap, "rgba(180,180,180,0.4)", "Other / Minority Interest")

    # Layer 6: Net Income / Loss
    if ni is not None:
        lbl    = "Net Income" if ni >= 0 else "Net Loss"
        color  = SANKEY_COLORS["net_income"] if ni >= 0 else SANKEY_COLORS["net_loss"]
        ni_idx = add_node(rich(lbl, ni), color, 1.0, 0.5)
        add_link(final_src, ni_idx, ni_w * down_scale * tn_scale,
                 "rgba(44,160,44,0.4)" if ni >= 0 else "rgba(214,39,40,0.4)",
                 f"{lbl}{_pct(ni, total_rev)}")

    if not link_src:
        return None
    return _make_sankey_data(nodes, colors, xs, ys,
                             link_src, link_tgt, link_val, link_col, link_lbl,
                             sd)


# ---------------------------------------------------------------------------
# Bank-specific Sankey
# ---------------------------------------------------------------------------

def _build_bank_sankey(sd: SegmentData, total_rev: float) -> Optional[SankeyData]:
    """
    Bank P&L flow:
      Segments → Net Revenue
        → Provision for Credit Losses
        → Revenue after Provision
            → expense sub-components (or single Non-Interest Expense)
            → Pre-tax Income
                → Income Tax
                → Net Income
    """
    nodes, colors, xs, ys = [], [], [], []
    link_src, link_tgt, link_val, link_col, link_lbl = [], [], [], [], []

    def add_node(label, color, x, y):
        nodes.append(label); colors.append(color); xs.append(x); ys.append(y)
        return len(nodes) - 1

    def add_link(src, tgt, val, color, label=""):
        if val is None or val <= 0:
            return
        min_v = max(total_rev * SANKEY_MIN_LINK_PCT, abs(val) * 0.001)
        link_src.append(src); link_tgt.append(tgt)
        link_val.append(max(abs(val), min_v))
        link_col.append(color); link_lbl.append(label)

    fmt  = _make_fmt(total_rev)
    rich = lambda lbl, v: f"{lbl}<br>{fmt(v)}{_pct(v, total_rev)}" if v else lbl

    # Layer 0+1: Revenue segments → Net Revenue.
    # When no segment data is available (e.g. Yahoo-only source), skip the segment
    # layer entirely and start directly from Net Revenue.
    has_segments = bool(sd.segments)
    rev_x = 0.15 if has_segments else 0.05
    rev_idx = add_node(rich("Net Revenue", total_rev), SANKEY_COLORS["total_revenue"], rev_x, 0.5)
    if has_segments:
        _add_segment_nodes(sd.segments, total_rev, rev_idx, add_node, add_link, rich)

    # Layer 2: Provision for Credit Losses (uses interest_expense slot in SegmentData)
    provision = sd.interest_expense   # mapped by pnl_from_bank_filing
    rev_after = sd.gross_profit       # mapped by pnl_from_bank_filing

    # Sanity check: provision should never exceed ~20% of net revenue.
    # Yahoo Finance stores gross interest expense in the interest_expense field;
    # for international banks (HSBC, SAN) this can be >50% of revenue — discard it.
    if provision is not None and provision > total_rev * BANK_PROVISION_MAX_PCT:
        provision = None
        rev_after = None   # recompute below without bad provision

    # Detect insurance pattern: large policyholder benefits / medical costs stored
    # in sd.cogs by pnl_from_insurance_filing.  Must be shown as an explicit outflow
    # from Net Revenue (not a provision) so the Sankey stays visually balanced.
    insurance_claims = None
    if (sd.cogs is not None and sd.cogs > 0 and sd.cogs > total_rev * INSURANCE_COGS_MIN_PCT
            and (provision is None or provision < total_rev * BANK_NII_MIN_PCT)):
        insurance_claims = sd.cogs

    # Derive rev_after if missing
    if rev_after is None:
        if insurance_claims:
            rev_after = total_rev - insurance_claims - (provision or 0)
        else:
            rev_after = total_rev - (provision or 0)

    prov_x = 0.35
    if insurance_claims:
        claims_idx = add_node(rich("Medical Costs & Claims", insurance_claims),
                              SANKEY_COLORS["cogs"], prov_x, 0.85)
        add_link(rev_idx, claims_idx, insurance_claims, "rgba(214,39,40,0.4)",
                 f"Medical Costs{_pct(insurance_claims, total_rev)}")
        rap_label = "Revenue after Claims"
    elif provision and provision > 0:
        prov_idx = add_node(rich("Provision for Credit Losses", provision),
                            SANKEY_COLORS["cogs"], prov_x, 0.85)
        add_link(rev_idx, prov_idx, provision, "rgba(214,39,40,0.4)",
                 f"Provision{_pct(provision, total_rev)}")
        rap_label = "Revenue after Provision"
    else:
        rap_label = "Revenue after Provision"

    rap_idx = add_node(rich(rap_label, rev_after),
                       SANKEY_COLORS["gross_profit"], prov_x, 0.35)
    add_link(rev_idx, rap_idx, rev_after, "rgba(44,160,44,0.4)",
             f"{rap_label}{_pct(rev_after, total_rev)}")

    # Layer 3: Non-Interest Expense (optionally sub-components)
    nie = sd.sga_expense   # mapped by pnl_from_bank_filing
    expense_detail = _read_expense_detail(sd)

    # Pre-read pre-tax income so we can compute the NIE budget via accounting identity:
    #   nie_budget = rev_after − pre_tax
    # Using this as link-width budget guarantees the Revenue-after-Provision node
    # stays visually balanced regardless of data inconsistencies in the raw filing.
    _op_pre = sd.operating_income
    if _op_pre is None and rev_after is not None and nie is not None:
        _op_pre = rev_after - nie
    nie_budget = None
    if _op_pre is not None and rev_after is not None:
        _implied = rev_after - abs(_op_pre)
        if _implied > total_rev * 0.01:
            nie_budget = _implied

    nie_source = rap_idx
    shown_exp_sum = 0.0   # total expense link-widths added from rap_idx

    if expense_detail and nie_budget is not None:
        exp_items = sorted(expense_detail.items(), key=lambda t: t[1], reverse=True)
        raw_sum  = sum(v for _, v in exp_items)
        # Scale link widths to fit budget (prevents overflow when items > budget).
        # Labels still display the original filing values.
        link_scale = (nie_budget / raw_sum) if raw_sum > nie_budget and raw_sum > 0 else 1.0

        MAX_NODES    = 6
        bucket_val   = 0.0
        bucket_names: List[str] = []
        n_vis = min(len(exp_items), MAX_NODES)

        for i, (exp_label, exp_val) in enumerate(exp_items):
            if i < MAX_NODES:
                y_pos = 0.04 + i * (0.72 / max(n_vis, 1))
                e_idx = add_node(rich(exp_label, exp_val),
                                 _expense_color(i), 0.62, y_pos)
                add_link(rap_idx, e_idx, exp_val * link_scale,
                         _expense_color(i, alpha=0.4),
                         f"{exp_label}{_pct(exp_val, total_rev)}")
                shown_exp_sum += exp_val * link_scale
            else:
                bucket_val  += exp_val * link_scale
                bucket_names.append(exp_label)

        if bucket_val > 0:
            raw_bucket = sum(v for _, v in exp_items[MAX_NODES:])
            oe_idx = add_node(rich("Other NIE", raw_bucket),
                              _expense_color(MAX_NODES), 0.62, 0.82)
            add_link(rap_idx, oe_idx, bucket_val,
                     _expense_color(MAX_NODES, alpha=0.4),
                     "Other: " + ", ".join(bucket_names))
            shown_exp_sum += bucket_val

        # Residual catch-all: any gap between budget and known sub-items
        residual = nie_budget - shown_exp_sum
        if residual > total_rev * 0.002:
            res_idx = add_node(rich("Other NIE", residual),
                               _expense_color(MAX_NODES + 1), 0.62, 0.90)
            add_link(rap_idx, res_idx, residual,
                     _expense_color(MAX_NODES + 1, alpha=0.4),
                     "Other Non-Interest Expenses")
            shown_exp_sum += residual

    elif nie_budget is not None:
        # No per-item detail — single expense bar sized by accounting identity
        exp_label = "Operating Expenses" if insurance_claims else "Non-Interest Expense"
        # Node label shows explicit NIE if available, else the implied value
        display_nie = nie if (nie and abs(nie - nie_budget) / max(nie_budget, 1) < 0.20) else nie_budget
        _e_idx = add_node(rich(exp_label, display_nie), SANKEY_COLORS["sga"], 0.62, 0.75)
        add_link(rap_idx, _e_idx, nie_budget, "rgba(255,127,14,0.4)",
                 f"{exp_label}{_pct(display_nie, total_rev)}")
        shown_exp_sum = nie_budget

    # Layer 4: Pre-tax / Operating Income
    op = _op_pre   # already computed above

    oi_idx = None
    if op is not None:
        lbl   = "Pre-tax Income" if op > 0 else "Pre-tax Loss"
        color = SANKEY_COLORS["operating_income"] if op > 0 else SANKEY_COLORS["net_loss"]
        oi_idx = add_node(rich(lbl, op), color, 0.78, 0.45)
        add_link(nie_source, oi_idx, abs(op),
                 "rgba(44,160,44,0.4)" if op > 0 else "rgba(214,39,40,0.4)",
                 f"{lbl}{_pct(op, total_rev)}")

    # Layer 5+6: Tax → Net Income
    final_src = oi_idx if oi_idx is not None else nie_source
    tax = sd.income_tax; ni = sd.net_income

    # Use explicit tax when positive; fall back to implied (op − ni) when tax is
    # missing or negative (e.g. deferred tax benefit) to keep Pre-tax node balanced.
    if (tax is None or tax <= 0) and op is not None and ni is not None and op > 0 and ni > 0 and op > ni:
        implied_tax = op - ni
        if implied_tax > total_rev * 0.005:
            tax = implied_tax

    # Enforce balance: scale link widths when tax + ni ≠ op (e.g. minority interest).
    # Labels always show real filing values; only link widths are adjusted.
    tax_w = abs(tax) if (tax and tax > 0) else 0.0
    ni_w  = abs(ni)  if ni is not None else 0.0
    total_down = tax_w + ni_w
    down_scale = 1.0
    if op is not None and op > 0 and total_down > 0:
        if total_down > op * 1.01:
            down_scale = op / total_down   # scale down (overflow)
        elif (op - total_down) > total_rev * 0.005:
            pass   # gap → add residual below

    if tax and tax > 0:
        t_idx = add_node(rich("Income Tax", tax), SANKEY_COLORS["tax"], 0.90, 0.65)
        add_link(final_src, t_idx, tax_w * down_scale, "rgba(127,127,127,0.4)", "Income Tax")
    if ni is not None:
        lbl   = "Net Income" if ni >= 0 else "Net Loss"
        color = SANKEY_COLORS["net_income"] if ni >= 0 else SANKEY_COLORS["net_loss"]
        ni_idx = add_node(rich(lbl, ni), color, 1.0, 0.5)
        add_link(final_src, ni_idx, ni_w * down_scale,
                 "rgba(44,160,44,0.4)" if ni >= 0 else "rgba(214,39,40,0.4)",
                 f"{lbl}{_pct(ni, total_rev)}")

    # Fill upward gap: if op > tax + ni, add an "Other" residual to close the node
    if op is not None and op > 0 and total_down > 0 and down_scale == 1.0:
        gap = op - total_down
        if gap > total_rev * 0.005:
            g_idx = add_node(rich("Other", gap), SANKEY_COLORS["tax"], 0.90, 0.80)
            add_link(final_src, g_idx, gap, "rgba(180,180,180,0.4)", "Other / Minority Interest")

    if not link_src:
        return None
    return _make_sankey_data(nodes, colors, xs, ys,
                             link_src, link_tgt, link_val, link_col, link_lbl,
                             sd)


# ---------------------------------------------------------------------------
# Yahoo-only financial Sankey (HSBC, SAN.MC, etc.)
# ---------------------------------------------------------------------------

def _read_bank_note(sd: SegmentData, prefix: str) -> Optional[float]:
    """Read a bank-specific value stored in sd.notes as 'PREFIX:value'."""
    for note in sd.notes:
        if note.startswith(f"{prefix}:"):
            try:
                return float(note[len(prefix) + 1:])
            except (ValueError, TypeError):
                pass
    return None


def _build_yahoo_financial_sankey(sd: SegmentData, total_rev: float) -> Optional[SankeyData]:
    """
    Bank-style P&L Sankey for Yahoo-only financial companies (HSBC, SAN.MC, etc.).

    Uses banking-specific terminology and builds as many layers as Yahoo data allows:
      [NII + Non-Int Income →] Net Revenue
        [→ Provision for Credit Losses]
        → Revenue after Provision
          → Non-Interest Expense
          → Pre-tax Income
            → Income Tax
            → Net Income
    """
    nodes, colors, xs, ys = [], [], [], []
    link_src, link_tgt, link_val, link_col, link_lbl = [], [], [], [], []

    def add_node(label, color, x, y):
        nodes.append(label); colors.append(color); xs.append(x); ys.append(y)
        return len(nodes) - 1

    def add_link(src, tgt, val, color, label=""):
        if val is None or val <= 0:
            return
        min_v = max(total_rev * SANKEY_MIN_LINK_PCT, abs(val) * 0.001)
        link_src.append(src); link_tgt.append(tgt)
        link_val.append(max(abs(val), min_v))
        link_col.append(color); link_lbl.append(label)

    fmt  = _make_fmt(total_rev)
    rich = lambda lbl, v: f"{lbl}<br>{fmt(v)}{_pct(v, total_rev)}" if v else lbl

    op  = sd.operating_income
    tax = sd.income_tax
    ni  = sd.net_income

    # Reconstruct pre-tax income from Net Income + Tax when Yahoo doesn't expose it
    if op is None and ni is not None and tax is not None and tax > 0:
        op = ni + tax

    # Bank-specific income components stored in notes by extract_for_yahoo_only
    bank_nii    = _read_bank_note(sd, "BANK_NII")
    bank_nonint = _read_bank_note(sd, "BANK_NONINT")

    # Provision for Credit Losses (stored in interest_expense slot, sanity-checked)
    provision = sd.interest_expense
    if provision is not None and (abs(provision) > total_rev * BANK_PROVISION_MAX_PCT or provision <= 0):
        provision = None

    # Non-Interest Expense — prefer explicit Yahoo field, fall back to implied
    nie = sd.sga_expense

    # Validate income split: NII + NonInt must account for 50–120% of total revenue
    has_income_split = (
        bank_nii is not None and bank_nonint is not None
        and bank_nii > 0 and bank_nonint > 0
        and 0.50 * total_rev <= bank_nii + bank_nonint <= 1.20 * total_rev
    )

    # X-positions cascade right based on how many layers are shown
    rev_x = 0.25 if has_income_split else 0.05

    # ── Layer 0: NII + Non-Interest Income → Net Revenue (optional) ──────────
    rev_idx = add_node(rich("Net Revenue", total_rev),
                       SANKEY_COLORS["total_revenue"], rev_x, 0.5)

    if has_income_split:
        income_sum   = bank_nii + bank_nonint
        s2r          = total_rev / income_sum   # scale so links sum to total_rev
        nii_idx      = add_node(rich("Net Interest Income", bank_nii),
                                SANKEY_COLORS["gross_profit"], 0.0, 0.28)
        nonint_idx   = add_node(rich("Non-Interest Income", bank_nonint),
                                _segment_color(1), 0.0, 0.72)
        add_link(nii_idx,    rev_idx, bank_nii    * s2r, "rgba(44,160,44,0.4)",
                 f"Net Interest Income{_pct(bank_nii, total_rev)}")
        add_link(nonint_idx, rev_idx, bank_nonint * s2r, "rgba(31,119,180,0.4)",
                 f"Non-Interest Income{_pct(bank_nonint, total_rev)}")

    # ── Layer 1: Provision for Credit Losses (optional) ──────────────────────
    flow_src  = rev_idx
    flow_base = total_rev

    if provision and provision > 0:
        rap     = total_rev - provision
        prov_x  = (0.48 if has_income_split else 0.35)
        prov_idx = add_node(rich("Provision for Credit Losses", provision),
                            SANKEY_COLORS["cogs"], prov_x, 0.88)
        add_link(rev_idx, prov_idx, provision, "rgba(214,39,40,0.4)",
                 f"Provision{_pct(provision, total_rev)}")
        rap_idx = add_node(rich("Revenue after Provision", rap),
                           SANKEY_COLORS["gross_profit"], prov_x, 0.35)
        add_link(rev_idx, rap_idx, rap, "rgba(44,160,44,0.4)",
                 f"Revenue after Provision{_pct(rap, total_rev)}")
        flow_src  = rap_idx
        flow_base = rap

    # ── Layer 2: Non-Interest Expense + Pre-tax Income ────────────────────────
    oi_idx = None
    if op is not None:
        # NIE = accounting identity: Total Revenue − Pre-tax Income.
        # This always ensures the Sankey is visually balanced.
        # Yahoo's expense fields may be incomplete sub-totals, so we always
        # use the implied value here for display correctness.
        computed_nie = flow_base - abs(op)

        exp_x = 0.65 if (has_income_split or provision) else 0.45

        if computed_nie > flow_base * 0.01:
            e_idx = add_node(rich("Non-Interest Expense", computed_nie),
                             SANKEY_COLORS["sga"], exp_x, 0.75)
            add_link(flow_src, e_idx, computed_nie, "rgba(214,39,40,0.4)",
                     f"Non-Interest Expense{_pct(computed_nie, total_rev)}")

        lbl   = "Pre-tax Income" if op >= 0 else "Pre-tax Loss"
        color = SANKEY_COLORS["operating_income"] if op >= 0 else SANKEY_COLORS["net_loss"]
        oi_idx = add_node(rich(lbl, op), color, exp_x, 0.25)
        add_link(flow_src, oi_idx, abs(op),
                 "rgba(44,160,44,0.4)" if op >= 0 else "rgba(214,39,40,0.4)",
                 f"{lbl}{_pct(op, total_rev)}")

    final_src = oi_idx if oi_idx is not None else flow_src

    # ── Layer 3: Income Tax + Net Income ─────────────────────────────────────
    # Fallback: use implied tax (op − ni) when explicit tax is missing/negative
    if (tax is None or tax <= 0) and op is not None and ni is not None and op > 0 and ni > 0 and op > ni:
        implied_tax = op - ni
        if implied_tax > total_rev * 0.005:
            tax = implied_tax

    # Enforce balance: scale outflows to match op when tax + ni ≠ op
    tax_w = abs(tax) if (tax and tax > 0) else 0.0
    ni_w  = abs(ni)  if ni is not None else 0.0
    total_down = tax_w + ni_w
    down_scale = 1.0
    if op is not None and op > 0 and total_down > 0 and total_down > op * 1.01:
        down_scale = op / total_down

    tax_x = 0.85
    if tax and tax > 0:
        t_idx = add_node(rich("Income Tax", tax), SANKEY_COLORS["tax"], tax_x, 0.68)
        add_link(final_src, t_idx, tax_w * down_scale, "rgba(127,127,127,0.4)", "Income Tax")
    if ni is not None:
        lbl_ni   = "Net Income" if ni >= 0 else "Net Loss"
        color_ni = SANKEY_COLORS["net_income"] if ni >= 0 else SANKEY_COLORS["net_loss"]
        ni_idx   = add_node(rich(lbl_ni, ni), color_ni, 1.0, 0.5)
        add_link(final_src, ni_idx, ni_w * down_scale,
                 "rgba(44,160,44,0.4)" if ni >= 0 else "rgba(214,39,40,0.4)",
                 f"{lbl_ni}{_pct(ni, total_rev)}")

    # Fill upward gap when op > tax + ni (minority interest / other)
    if op is not None and op > 0 and total_down > 0 and down_scale == 1.0:
        gap = op - total_down
        if gap > total_rev * 0.005:
            g_idx = add_node(rich("Other", gap), SANKEY_COLORS["tax"], tax_x, 0.80)
            add_link(final_src, g_idx, gap, "rgba(180,180,180,0.4)", "Other / Minority Interest")

    if not link_src:
        return None
    return _make_sankey_data(nodes, colors, xs, ys,
                             link_src, link_tgt, link_val, link_col, link_lbl, sd)


# ---------------------------------------------------------------------------
# Shared segment-node builder
# ---------------------------------------------------------------------------

def _add_segment_nodes(segments, total_rev, rev_idx, add_node, add_link, rich):
    """Add segment nodes (Layer 0) flowing into the revenue node."""
    if not segments:
        single = add_node(rich("Revenue", total_rev), SANKEY_COLORS["segment"], 0.0, 0.5)
        add_link(single, rev_idx, total_rev, SANKEY_COLORS["segment"])
        return

    MIN_PCT  = 0.015
    MAX_SEGS = 8
    sorted_segs = sorted(
        [s for s in segments if s.value and s.value > 0],
        key=lambda s: s.value, reverse=True,
    )
    visible: List = []
    other_val = 0.0
    other_names: List[str] = []
    for s in sorted_segs:
        is_tiny = total_rev and s.value / total_rev < MIN_PCT
        if is_tiny or len(visible) >= MAX_SEGS:
            other_val += s.value
            other_names.append(s.segment_name)
        else:
            visible.append(s)

    n_display = len(visible) + (1 if other_val > 0 else 0)

    # Normalize link widths so the revenue node height matches its incoming links.
    # Scale down overshoots AND scale up small undershoots (≤ 30% gap) so the
    # Sankey node stays visually balanced.  Labels keep the real values; only the
    # rendered link width is scaled.  Gaps > 30% get an Unallocated node instead.
    vis_sum = sum(s.value for s in visible if s.value) + other_val
    link_scale = (total_rev / vis_sum) if vis_sum > 0 else 1.0
    if link_scale > 1.30:
        link_scale = 1.0   # gap too large to fill by scaling; show Unallocated below

    for i, seg in enumerate(visible):
        y_pos   = (i + 0.5) / max(n_display, 1)
        seg_idx = add_node(rich(seg.segment_name, seg.value),
                           _segment_color(i), 0.0, y_pos)
        add_link(seg_idx, rev_idx, seg.value * link_scale, _segment_color(i, alpha=0.4),
                 f"{seg.segment_name}{_pct(seg.value, total_rev)}")

    if other_val > 0:
        label = "Other" if len(other_names) <= 3 else f"Other ({len(other_names)} segments)"
        y_pos  = (len(visible) + 0.5) / max(n_display, 1)
        o_idx  = add_node(rich(label, other_val), _segment_color(len(visible)), 0.0, y_pos)
        add_link(o_idx, rev_idx, other_val * link_scale, _segment_color(len(visible), alpha=0.4),
                 "Other: " + ", ".join(other_names))

    # Unallocated gap > 5% (only when segments fall short, not when they overshoot)
    seg_total = vis_sum * link_scale   # = total_rev when overshooting, else vis_sum
    gap = total_rev - seg_total
    if gap > total_rev * 0.05:
        g_idx = add_node(rich("Unallocated", gap),
                         _segment_color(len(visible) + 1), 0.0, 0.98)
        add_link(g_idx, rev_idx, gap, _segment_color(len(visible) + 1, alpha=0.4))


# ---------------------------------------------------------------------------
# 2-layer segment nodes (sub-products → top segments → revenue)
# ---------------------------------------------------------------------------

def _add_two_layer_segment_nodes(
    top_segs, sub_segs, hierarchy, total_rev, rev_idx, add_node, add_link, rich,
):
    """Render 2-layer segment Sankey: sub-products flow into top segments → revenue.

    Layout:
      sub-products  x=0.0
      top-segments  x=0.13
      revenue       x=0.25  (set by caller)
    """
    if not top_segs or not sub_segs or not hierarchy:
        _add_segment_nodes(top_segs, total_rev, rev_idx, add_node, add_link, rich)
        return

    # ── Top-segment layer (x=0.13) ──────────────────────────────────────────
    sorted_top = sorted(
        [s for s in top_segs if s.value and s.value > 0],
        key=lambda s: s.value, reverse=True,
    )
    n_top = len(sorted_top)
    top_idx_map: dict = {}
    for i, seg in enumerate(sorted_top):
        y = (i + 0.5) / max(n_top, 1)
        cidx = add_node(rich(seg.segment_name, seg.value), _segment_color(i), 0.13, y)
        top_idx_map[seg.segment_name] = (cidx, i)
        # top-segment → revenue
        add_link(cidx, rev_idx, seg.value, _segment_color(i, alpha=0.4),
                 f"{seg.segment_name}{_pct(seg.value, total_rev)}")

    # ── Sub-product layer (x=0.0) ────────────────────────────────────────────
    sub_map = {s.segment_name: s for s in sub_segs if s.value and s.value > 0}

    # Group sub-products by their parent top-segment (order by top-seg y-position)
    ordered_groups: List[tuple] = []  # (top_seg_name, [sub_names])
    for seg in sorted_top:
        children = [n for n in hierarchy.get(seg.segment_name, []) if n in sub_map]
        if children:
            ordered_groups.append((seg.segment_name, children))

    # Assign y-positions: within each group, distribute proportionally
    all_sub_items: List[tuple] = []  # (sub_name, parent_name)
    for top_name, children in ordered_groups:
        for child in children:
            all_sub_items.append((child, top_name))

    # Any unassigned sub-products go at the bottom
    assigned = {name for _, children in ordered_groups for name in children}
    unassigned = [s.segment_name for s in sorted(sub_segs, key=lambda s: s.value or 0, reverse=True)
                  if s.segment_name not in assigned and s.segment_name in sub_map]
    if unassigned:
        all_sub_items.extend((name, None) for name in unassigned)

    n_sub = len(all_sub_items)
    for j, (sub_name, parent_name) in enumerate(all_sub_items):
        sv = sub_map.get(sub_name)
        if sv is None:
            continue
        y = (j + 0.5) / max(n_sub, 1)
        color_i = top_idx_map[parent_name][1] if parent_name and parent_name in top_idx_map else len(sorted_top)
        s_idx = add_node(rich(sub_name, sv.value), _segment_color(color_i), 0.0, y)

        if parent_name and parent_name in top_idx_map:
            top_cidx = top_idx_map[parent_name][0]
            add_link(s_idx, top_cidx, sv.value, _segment_color(color_i, alpha=0.4),
                     f"{sub_name}{_pct(sv.value, total_rev)}")
        else:
            # Unassigned: link directly to revenue
            add_link(s_idx, rev_idx, sv.value, _segment_color(color_i, alpha=0.4),
                     f"{sub_name}{_pct(sv.value, total_rev)}")


# ---------------------------------------------------------------------------
# Segment picker (geo vs product)
# ---------------------------------------------------------------------------

def _pick_best_segments(segments: list, total_rev: float) -> list:
    """
    When multiple revenue hierarchies are present (e.g. geo + product),
    pick the most business-meaningful set that sums closest to total_rev.
    Prefers product/service categories over geographic breakdowns.
    """
    if not segments or not total_rev:
        return segments

    seg_sum = sum(s.value for s in segments if s.value)
    if seg_sum <= total_rev * 1.10:
        return segments

    geo_segs     = [s for s in segments if any(k in s.segment_name.lower() for k in _GEO_KEYWORDS)]
    non_geo_segs = [s for s in segments if s not in geo_segs]

    if non_geo_segs:
        ng_sum = sum(s.value for s in non_geo_segs if s.value)
        if 0.70 * total_rev <= ng_sum <= 1.30 * total_rev:
            return non_geo_segs

    if geo_segs:
        g_sum = sum(s.value for s in geo_segs if s.value)
        if 0.70 * total_rev <= g_sum <= 1.30 * total_rev:
            return geo_segs

    # Fallback: greedily include largest until ~total_rev
    sorted_segs = sorted(segments, key=lambda s: s.value or 0, reverse=True)
    cumsum, result = 0.0, []
    for s in sorted_segs:
        cumsum += s.value or 0
        result.append(s)
        if cumsum >= total_rev * 0.90:
            break
    return result


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _read_expense_detail(sd: SegmentData) -> Optional[Dict[str, float]]:
    """Extract EXPENSE_DETAIL from sd.notes (written by extraction_router)."""
    for note in sd.notes:
        if note.startswith("EXPENSE_DETAIL:"):
            try:
                return json.loads(note[len("EXPENSE_DETAIL:"):])
            except Exception:
                pass
    return None


def _make_fmt(total_rev: float):
    def _fmt(val: Optional[float]) -> str:
        if val is None:
            return ""
        v = abs(val)
        sign = "-" if val < 0 else ""
        if v >= 1e12: return f"{sign}${v/1e12:.2f}T"
        if v >= 1e9:  return f"{sign}${v/1e9:.1f}B"
        if v >= 1e6:  return f"{sign}${v/1e6:.0f}M"
        if v >= 1e3:  return f"{sign}${v/1e3:.0f}K"
        return f"{sign}${v:.0f}"
    return _fmt


def _pct(val: Optional[float], total_rev: float) -> str:
    if val is None or not total_rev:
        return ""
    return f" ({val / total_rev * 100:.1f}%)"


def _make_sankey_data(nodes, colors, xs, ys, ls, lt, lv, lc, ll, sd) -> SankeyData:
    return SankeyData(
        node_labels  = nodes,  node_colors  = colors,
        node_x       = xs,     node_y       = ys,
        link_sources = ls,     link_targets = lt,
        link_values  = lv,     link_colors  = lc,
        link_labels  = ll,
        period       = sd.period,
        ticker       = sd.ticker,
        currency     = sd.currency,
    )


def _segment_color(index: int, alpha: float = 1.0) -> str:
    blues = ["#1f77b4", "#4292c6", "#6baed6", "#9ecae1", "#c6dbef",
             "#2171b5", "#084594", "#2c7fb8", "#41b6c4", "#7fcdbb"]
    base = blues[index % len(blues)]
    if alpha < 1.0:
        r, g, b = int(base[1:3], 16), int(base[3:5], 16), int(base[5:7], 16)
        return f"rgba({r},{g},{b},{alpha})"
    return base


def _expense_color(index: int, alpha: float = 1.0) -> str:
    """Orange-red palette for expense sub-components."""
    palette = ["#e6550d", "#fd8d3c", "#fdae6b", "#fdd0a2", "#843c39", "#ad494a"]
    base = palette[index % len(palette)]
    if alpha < 1.0:
        r, g, b = int(base[1:3], 16), int(base[3:5], 16), int(base[5:7], 16)
        return f"rgba({r},{g},{b},{alpha})"
    return base