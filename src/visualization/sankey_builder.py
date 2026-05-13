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
from config import SANKEY_COLORS, SANKEY_MIN_LINK_PCT
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

    if is_financial:
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

    # Layer 0+1: Revenue segments → Total Revenue
    rev_idx = add_node(rich("Total Revenue", total_rev), SANKEY_COLORS["total_revenue"], 0.2, 0.5)
    _add_segment_nodes(sd.segments, total_rev, rev_idx, add_node, add_link, rich)

    # Layer 2: Gross Profit ←→ COGS
    gp = sd.gross_profit; cogs = sd.cogs
    op = sd.operating_income; rd = sd.rd_expense; sga = sd.sga_expense
    if gp is not None and cogs is None:   cogs = total_rev - gp
    elif cogs is not None and gp is None: gp   = total_rev - cogs
    elif gp is None and cogs is None and op is not None:
        opex = (rd or 0) + (sga or 0)
        gp   = op + opex
        cogs = total_rev - gp

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

    # Layer 5: Below-the-line
    final_src = oi_idx if oi_idx is not None else gp_src
    ie = sd.interest_expense; tax = sd.income_tax; ni = sd.net_income
    if ie and ie > 0:
        i_idx = add_node(rich("Interest & Other", ie), SANKEY_COLORS["interest"], 0.87, 0.35)
        add_link(final_src, i_idx, ie, "rgba(127,127,127,0.4)", "Interest & Other")
    if tax and tax > 0:
        t_idx = add_node(rich("Income Tax", tax), SANKEY_COLORS["tax"], 0.87, 0.65)
        add_link(final_src, t_idx, tax, "rgba(127,127,127,0.4)", "Income Tax")

    # Layer 6: Net Income / Loss
    if ni is not None:
        lbl    = "Net Income" if ni >= 0 else "Net Loss"
        color  = SANKEY_COLORS["net_income"] if ni >= 0 else SANKEY_COLORS["net_loss"]
        ni_idx = add_node(rich(lbl, ni), color, 1.0, 0.5)
        add_link(final_src, ni_idx, abs(ni),
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

    # Layer 0+1: Revenue segments → Net Revenue
    rev_idx = add_node(rich("Net Revenue", total_rev), SANKEY_COLORS["total_revenue"], 0.15, 0.5)
    _add_segment_nodes(sd.segments, total_rev, rev_idx, add_node, add_link, rich)

    # Layer 2: Provision for Credit Losses (uses interest_expense slot in SegmentData)
    provision = sd.interest_expense   # mapped by pnl_from_bank_filing
    rev_after = sd.gross_profit       # mapped by pnl_from_bank_filing

    # Derive rev_after_provision if missing
    if rev_after is None:
        rev_after = total_rev - (provision or 0)

    prov_x = 0.35
    if provision and provision > 0:
        prov_idx = add_node(rich("Provision for Credit Losses", provision),
                            SANKEY_COLORS["cogs"], prov_x, 0.85)
        add_link(rev_idx, prov_idx, provision, "rgba(214,39,40,0.4)",
                 f"Provision{_pct(provision, total_rev)}")

    rap_idx = add_node(rich("Revenue after Provision", rev_after),
                       SANKEY_COLORS["gross_profit"], prov_x, 0.35)
    add_link(rev_idx, rap_idx, rev_after, "rgba(44,160,44,0.4)",
             f"After Provision{_pct(rev_after, total_rev)}")

    # Layer 3: Non-Interest Expense (optionally sub-components)
    nie = sd.sga_expense   # mapped by pnl_from_bank_filing
    expense_detail = _read_expense_detail(sd)

    nie_source = rap_idx
    shown_exp_sum = 0.0   # total expense links added from rap_idx

    if expense_detail:
        # Sort descending; spread y-positions evenly in top 75% of chart
        exp_items = sorted(expense_detail.items(), key=lambda t: t[1], reverse=True)
        MAX_NODES   = 6      # individual nodes before bucketing
        bucket_val  = 0.0
        bucket_names: List[str] = []
        n_vis = min(len(exp_items), MAX_NODES)

        for i, (exp_label, exp_val) in enumerate(exp_items):
            if i < MAX_NODES:
                y_pos = 0.04 + i * (0.72 / max(n_vis, 1))
                e_idx = add_node(rich(exp_label, exp_val),
                                 _expense_color(i), 0.62, y_pos)
                add_link(rap_idx, e_idx, exp_val,
                         _expense_color(i, alpha=0.4),
                         f"{exp_label}{_pct(exp_val, total_rev)}")
                shown_exp_sum += exp_val
            else:
                bucket_val  += exp_val
                bucket_names.append(exp_label)

        if bucket_val > 0:
            oe_idx = add_node(rich("Other NIE", bucket_val),
                              _expense_color(MAX_NODES), 0.62, 0.82)
            add_link(rap_idx, oe_idx, bucket_val,
                     _expense_color(MAX_NODES, alpha=0.4),
                     "Other: " + ", ".join(bucket_names))
            shown_exp_sum += bucket_val

        # Gap-closer: residual = NIE - sum(all known sub-components).
        # Present in all banks as "Other Non-Interest Expense" catch-all.
        if nie and nie > 0:
            residual = nie - shown_exp_sum
            if residual > total_rev * 0.002:   # > 0.2% threshold
                res_idx = add_node(rich("Other NIE", residual),
                                   _expense_color(MAX_NODES + 1), 0.62, 0.90)
                add_link(rap_idx, res_idx, residual,
                         _expense_color(MAX_NODES + 1, alpha=0.4),
                         "Other Non-Interest Expenses")
                shown_exp_sum += residual

    elif nie and nie > 0:
        nie_idx = add_node(rich("Non-Interest Expense", nie),
                           SANKEY_COLORS["sga"], 0.62, 0.75)
        add_link(rap_idx, nie_idx, nie, "rgba(255,127,14,0.4)",
                 f"Non-Interest Expense{_pct(nie, total_rev)}")
        shown_exp_sum = nie

    # Layer 4: Pre-tax / Operating Income
    op = sd.operating_income
    if op is None and rev_after is not None and nie is not None:
        op = rev_after - nie

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
    if tax and tax > 0:
        t_idx = add_node(rich("Income Tax", tax), SANKEY_COLORS["tax"], 0.90, 0.65)
        add_link(final_src, t_idx, tax, "rgba(127,127,127,0.4)", "Income Tax")
    if ni is not None:
        lbl   = "Net Income" if ni >= 0 else "Net Loss"
        color = SANKEY_COLORS["net_income"] if ni >= 0 else SANKEY_COLORS["net_loss"]
        ni_idx = add_node(rich(lbl, ni), color, 1.0, 0.5)
        add_link(final_src, ni_idx, abs(ni),
                 "rgba(44,160,44,0.4)" if ni >= 0 else "rgba(214,39,40,0.4)",
                 f"{lbl}{_pct(ni, total_rev)}")

    if not link_src:
        return None
    return _make_sankey_data(nodes, colors, xs, ys,
                             link_src, link_tgt, link_val, link_col, link_lbl,
                             sd)


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
    for i, seg in enumerate(visible):
        y_pos   = (i + 0.5) / max(n_display, 1)
        seg_idx = add_node(rich(seg.segment_name, seg.value),
                           _segment_color(i), 0.0, y_pos)
        add_link(seg_idx, rev_idx, seg.value, _segment_color(i, alpha=0.4),
                 f"{seg.segment_name}{_pct(seg.value, total_rev)}")

    if other_val > 0:
        label = "Other" if len(other_names) <= 3 else f"Other ({len(other_names)} segments)"
        y_pos  = (len(visible) + 0.5) / max(n_display, 1)
        o_idx  = add_node(rich(label, other_val), _segment_color(len(visible)), 0.0, y_pos)
        add_link(o_idx, rev_idx, other_val, _segment_color(len(visible), alpha=0.4),
                 "Other: " + ", ".join(other_names))

    # Unallocated gap > 5%
    seg_total = sum(s.value for s in visible if s.value) + other_val
    gap = total_rev - seg_total
    if gap > total_rev * 0.05:
        g_idx = add_node(rich("Unallocated", gap),
                         _segment_color(len(visible) + 1), 0.0, 0.98)
        add_link(g_idx, rev_idx, gap, _segment_color(len(visible) + 1, alpha=0.4))


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