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
            autosize = True,
            height = 780,
            margin = dict(l=20, r=20, t=20, b=20),
            paper_bgcolor = "rgba(0,0,0,0)",
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
            "scrollZoom":     False,
            "displayModeBar": False,
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
  .zoom-controls {{ display:flex; align-items:center; gap:6px; margin-left:auto; }}
  .zoom-btn {{ background:white; border:1px solid #ccc; border-radius:5px; padding:4px 10px;
               font-size:15px; font-weight:700; cursor:pointer; color:#333; line-height:1; }}
  .zoom-btn:hover {{ background:#f0f0f0; }}
  .zoom-pct {{ font-size:12px; color:#666; min-width:40px; text-align:center; }}
  .zoom-hint {{ font-size:11px; color:#999; }}
  #zoom-wrapper {{ overflow:hidden; border-radius:0 0 8px 8px; background:white;
                   height:780px; position:relative; }}
  #zoom-target {{ transform-origin:0 0; width:100%; height:100%; }}
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>{company_name} <span style="opacity:0.7">({ticker})</span></h1>
    <div class="source-note">Data sources: SEC EDGAR (10-K/10-Q/20-F) + Yahoo Finance</div>
  </div>
</div>

<div class="controls" style="display:flex; align-items:center; flex-wrap:wrap; gap:10px;">
  <div>
    <label for="period-select">Fiscal Year:</label>
    <select id="period-select" onchange="switchPeriod(this.value)">
      {options_html}
    </select>
  </div>
  <span id="data-badge" style="font-size:12px; color:#666;"></span>
  <div class="zoom-controls">
    <span class="zoom-hint">Scroll to zoom · Right-drag to pan</span>
    <button class="zoom-btn" onclick="zoomOut()" title="Zoom out (-)">−</button>
    <span class="zoom-pct" id="zoom-pct">100%</span>
    <button class="zoom-btn" onclick="zoomIn()" title="Zoom in (+)">+</button>
    <button class="zoom-btn" onclick="resetZoom()" title="Reset view (R)" style="font-size:12px; padding:4px 8px;">Reset</button>
  </div>
</div>

<div class="chart-container" style="padding:0; overflow:hidden;">
  <div id="zoom-wrapper">
    <div id="zoom-target">
      {fig_html}
    </div>
  </div>
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

  Plotly.update("sankey-chart", update, {{}});

  var badge = document.getElementById("data-badge");
  if (data.has_partial) {{
    badge.innerHTML = '<span class="badge">⚠ Partial Data</span> ' + data.coverage_note;
  }} else {{
    badge.textContent = data.coverage_note || "";
  }}
}}

// Set initial badge
switchPeriod(document.getElementById("period-select").value);

// ── Zoom + Pan ───────────────────────────────────────────────────────────────
(function() {{
  var wrapper = document.getElementById('zoom-wrapper');
  var target  = document.getElementById('zoom-target');
  var scale = 1.0, tx = 0, ty = 0;
  var dragging = false;
  var dragStartX, dragStartY, dragStartTx, dragStartTy;

  function applyXform() {{
    target.style.transform = 'translate(' + tx + 'px,' + ty + 'px) scale(' + scale + ')';
    document.getElementById('zoom-pct').textContent = Math.round(scale * 100) + '%';
  }}

  // Mouse-wheel zoom centred on cursor
  wrapper.addEventListener('wheel', function(e) {{
    e.preventDefault();
    var rect = wrapper.getBoundingClientRect();
    var mx = e.clientX - rect.left - tx;
    var my = e.clientY - rect.top  - ty;
    var factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    var ns = Math.min(6, Math.max(0.2, scale * factor));
    tx -= mx * (ns / scale - 1);
    ty -= my * (ns / scale - 1);
    scale = ns;
    applyXform();
  }}, {{passive: false}});

  // Right-click drag to pan (avoids conflict with Plotly left-click node drag)
  wrapper.addEventListener('mousedown', function(e) {{
    if (e.button !== 2) return;
    e.preventDefault();
    dragging = true;
    dragStartX = e.clientX; dragStartY = e.clientY;
    dragStartTx = tx;       dragStartTy = ty;
    wrapper.style.cursor = 'grabbing';
  }});
  document.addEventListener('mousemove', function(e) {{
    if (!dragging) return;
    tx = dragStartTx + (e.clientX - dragStartX);
    ty = dragStartTy + (e.clientY - dragStartY);
    applyXform();
  }});
  document.addEventListener('mouseup', function(e) {{
    if (e.button === 2 && dragging) {{
      dragging = false;
      wrapper.style.cursor = '';
    }}
  }});
  wrapper.addEventListener('contextmenu', function(e) {{ e.preventDefault(); }});

  // Keyboard shortcuts
  document.addEventListener('keydown', function(e) {{
    var tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'select' || tag === 'textarea') return;
    if (e.key === '=' || e.key === '+') {{ scale = Math.min(6, scale * 1.15); applyXform(); }}
    if (e.key === '-' || e.key === '_') {{ scale = Math.max(0.2, scale / 1.15); applyXform(); }}
    if (e.key === '0' || e.key.toLowerCase() === 'r') {{ scale=1; tx=0; ty=0; applyXform(); }}
    if (e.key === 'ArrowLeft')  {{ tx += 40; applyXform(); }}
    if (e.key === 'ArrowRight') {{ tx -= 40; applyXform(); }}
    if (e.key === 'ArrowUp')    {{ ty += 40; applyXform(); }}
    if (e.key === 'ArrowDown')  {{ ty -= 40; applyXform(); }}
  }});

  // Exposed to button onclick handlers
  window.zoomIn    = function() {{ scale = Math.min(6, scale * 1.2); applyXform(); }};
  window.zoomOut   = function() {{ scale = Math.max(0.2, scale / 1.2); applyXform(); }};
  window.resetZoom = function() {{ scale=1; tx=0; ty=0; applyXform(); }};
}})();
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
