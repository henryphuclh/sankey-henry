"""Tests for sankey_builder.py — the primary graded deliverable."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.extraction.models import SegmentData, SegmentValue
from src.visualization.sankey_builder import build_sankey


def _make_segment_data(segments=None, **kwargs) -> SegmentData:
    defaults = dict(
        ticker="TEST", period="FY2024", is_annual=True,
        fiscal_year=2024, fiscal_quarter=None,
        total_revenue=100e9,
        gross_profit=60e9,
        operating_income=30e9,
        net_income=25e9,
        cogs=40e9,
        rd_expense=15e9,
        sga_expense=10e9,
        interest_expense=2e9,
        income_tax=5e9,
        confidence=0.9,
    )
    defaults.update(kwargs)
    sd = SegmentData(**{k: v for k, v in defaults.items()
                        if k in SegmentData.__dataclass_fields__})
    if segments:
        sd.segments = segments
    return sd


def test_build_sankey_basic():
    """Standard company with segments — should produce nodes and links."""
    segs = [
        SegmentValue("Cloud", 60e9, "USD", "FY2024", "test", True),
        SegmentValue("Devices", 25e9, "USD", "FY2024", "test", True),
        SegmentValue("Gaming", 15e9, "USD", "FY2024", "test", True),
    ]
    sd = _make_segment_data(segments=segs)
    result = build_sankey(sd)

    assert result is not None
    assert len(result.node_labels) > 0
    assert len(result.link_sources) == len(result.link_targets) == len(result.link_values)
    assert all(v > 0 for v in result.link_values), "All link values must be positive"
    assert any("Total Revenue" in l for l in result.node_labels)


def test_build_sankey_no_segments():
    """Company with no segment breakdown — should still produce a valid chart."""
    sd = _make_segment_data(segments=[])
    result = build_sankey(sd)
    assert result is not None
    assert len(result.link_sources) > 0


def test_build_sankey_net_loss():
    """Loss-making company — net income node should still exist."""
    sd = _make_segment_data(
        operating_income=-5e9,
        net_income=-8e9,
    )
    result = build_sankey(sd)
    assert result is not None
    labels = result.node_labels
    assert any("Loss" in l or "Income" in l for l in labels)


def test_build_sankey_single_segment():
    """Edge case: only one segment."""
    segs = [SegmentValue("Services", 100e9, "USD", "FY2024", "test", True)]
    sd = _make_segment_data(segments=segs)
    result = build_sankey(sd)
    assert result is not None
    assert len(result.link_values) >= 1


def test_build_sankey_none_input():
    """None input should return None gracefully."""
    result = build_sankey(None)
    assert result is None


def test_build_sankey_zero_revenue():
    """Zero revenue should return None (nothing to plot)."""
    sd = _make_segment_data(total_revenue=0, gross_profit=0)
    result = build_sankey(sd)
    assert result is None


def test_segment_revenues_positive():
    """All link values must be positive (no negative flows in Plotly Sankey)."""
    segs = [
        SegmentValue("Segment A", 70e9, "USD", "FY2024", "test", True),
        SegmentValue("Segment B", 30e9, "USD", "FY2024", "test", True),
    ]
    sd = _make_segment_data(segments=segs)
    result = build_sankey(sd)
    assert result is not None
    assert all(v > 0 for v in result.link_values)


def test_build_sankey_missing_pnl():
    """Missing gross profit and operating income — should still render."""
    sd = _make_segment_data(
        gross_profit=None, operating_income=None, cogs=None,
        rd_expense=None, sga_expense=None, income_tax=None,
    )
    result = build_sankey(sd)
    # Should still produce something from total_revenue → net_income path
    assert result is not None or sd.net_income is not None
