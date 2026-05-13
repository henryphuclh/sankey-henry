"""Render SankeyData as an interactive Plotly HTML with period dropdown."""
from __future__ import annotations

from typing import Dict, List, Optional
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from src.visualization.sankey_builder import SankeyData


def render_sankey_html(
    sankey_by_period: Dict[str, Optional[SankeyData]],
    company_name:     str,
    ticker:           str,
) -> str:
    """
    Build a full HTML page with:
    - Period dropdown (FY2022 / FY2023 / FY2024)
    - Plotly go.Sankey (freeform arrangement — draggable nodes, scroll zoom)
    - All period data embedded as JSON for client-side switching
    """
    import plotly.graph_objects as go
    import json

    # Build one Plotly figure per period; serialize to JSON
    period_data: Dict[str, dict] = {}
    for period, sd in sankey_by_period.items():
        if sd is None:
            continue
        fig_data = _build_fig_data(sd, company_name, period)
        period_data[period] = fig_data

    if not period_data:
        return _no_data_html(company_name, ticker)

    default_period = sorted(period_data.keys(), reverse=True)[0]
    default_data   = period_data[default_period]

    # Serialize all period data as JSON for JS switching
    all_data_json = json.dumps(period_data, ensure_ascii=False)
    periods_list  = sorted(period_data.keys(), reverse=True)

    # Build dropdown options HTML
    options_html = "\n".join(
        f'<option value="{p}"{" selected" if p == default_period else ""}>{p}</option>'
        for p in periods_list
    )

    # Build initial figure as HTML snippet
    initial_fig = go.Figure(
        data=[go.Sankey(
            arrangement = "snap",
            node = dict(
                pad       = 28,
                thickness = 18,
                line      = dict(color="black", width=0.5),
                label     = default_data["node_labels"],
                color     = default_data["node_colors"],
                hovertemplate = "%{label}<br>Value: %{value:,.0f}<extra></extra>",
            ),
            link = dict(
                source        = default_data["link_sources"],
                target        = default_data["link_targets"],
                value         = default_data["link_values"],
                color         = default_data["link_colors"],
                label         = default_data["link_labels"],
                hovertemplate = "%{label}<br>%{source.label} → %{target.label}<extra></extra>",
            ),
        )],
        layout = go.Layout(
            title_text = f"{company_name} ({ticker}) — Financial Flow {default_period}",
            title_font_size = 16,
            autosize = True,
            height = 780,
            margin = dict(l=20, r=20, t=60, b=20),
            paper_bgcolor = "#fafafa",
            font = dict(size=12),
        ),
    )

    fig_html = initial_fig.to_html(
        full_html   = False,
        include_plotlyjs = "cdn",
        div_id      = "sankey-chart",
        default_width  = "100%",
        default_height = "780px",
        config      = {
            "responsive":     True,
            "scrollZoom":     True,
            "displayModeBar": True,
            "modeBarButtonsToAdd": ["zoom2d", "pan2d", "resetScale2d"],
        },
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{company_name} ({ticker}) — Sankey Financial Flow</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          margin: 0; padding: 16px; background: #f5f5f5; }}
  .header {{ background: #1a1a2e; color: white; padding: 16px 24px; border-radius: 8px;
             margin-bottom: 16px; display: flex; align-items: center; gap: 16px; }}
  .header h1 {{ margin: 0; font-size: 1.4rem; }}
  .controls {{ background: white; padding: 12px 20px; border-radius: 8px;
               margin-bottom: 12px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }}
  .controls label {{ font-weight: 600; margin-right: 10px; color: #333; }}
  select {{ padding: 6px 14px; font-size: 14px; border-radius: 6px;
            border: 1px solid #ccc; cursor: pointer; }}
  .chart-container {{ background: white; border-radius: 8px;
                      box-shadow: 0 1px 4px rgba(0,0,0,0.08); padding: 8px; }}
  .badge {{ background: #ff9800; color: white; font-size: 11px; padding: 2px 8px;
            border-radius: 10px; }}
  .source-note {{ font-size: 11px; color: #888; margin-top: 8px; }}
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>{company_name} <span style="opacity:0.7">({ticker})</span></h1>
    <div class="source-note">Data sources: SEC EDGAR (10-K/10-Q/20-F) + Yahoo Finance</div>
  </div>
</div>

<div class="controls">
  <label for="period-select">Fiscal Year:</label>
  <select id="period-select" onchange="switchPeriod(this.value)">
    {options_html}
  </select>
  <span id="data-badge" style="margin-left:12px; font-size:12px; color:#666;"></span>
</div>

<div class="chart-container">
  {fig_html}
</div>

<script>
var ALL_DATA = {all_data_json};

function switchPeriod(period) {{
  var data = ALL_DATA[period];
  if (!data) return;

  var update = {{
    "node.label":  [data.node_labels],
    "node.color":  [data.node_colors],
    "link.source": [data.link_sources],
    "link.target": [data.link_targets],
    "link.value":  [data.link_values],
    "link.color":  [data.link_colors],
    "link.label":  [data.link_labels],
  }};

  var layoutUpdate = {{
    "title.text": data.title,
  }};

  Plotly.update("sankey-chart", update, layoutUpdate);

  var badge = document.getElementById("data-badge");
  if (data.has_partial) {{
    badge.innerHTML = '<span class="badge">⚠ Partial Data</span> ' + data.coverage_note;
  }} else {{
    badge.textContent = data.coverage_note || "";
  }}
}}

// Set initial badge
switchPeriod(document.getElementById("period-select").value);
</script>
</body>
</html>"""

    return html


def _build_fig_data(sd: SankeyData, company_name: str, period: str) -> dict:
    """Serialize SankeyData to a plain dict for JSON embedding in HTML."""
    return {
        "node_labels":  sd.node_labels,
        "node_colors":  sd.node_colors,
        "node_x":       sd.node_x,
        "node_y":       sd.node_y,
        "link_sources": sd.link_sources,
        "link_targets": sd.link_targets,
        "link_values":  sd.link_values,
        "link_colors":  sd.link_colors,
        "link_labels":  sd.link_labels,
        "title":        f"{company_name} ({sd.ticker}) — Financial Flow {period}",
        "currency":     sd.currency,
        "has_partial":  False,
        "coverage_note": f"{period}",
    }


def _no_data_html(company_name: str, ticker: str) -> str:
    return f"""<!DOCTYPE html><html><head><title>{company_name}</title></head>
<body><h2>{company_name} ({ticker})</h2>
<p style="color:red">No financial data available to render Sankey chart.</p>
</body></html>"""
