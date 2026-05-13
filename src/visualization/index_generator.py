"""Build a dashboard index.html listing every generated company report.

Scans OUTPUT_DIR for `{TICKER}_sankey.html` files and produces a sortable,
searchable index with a snapshot row per company (revenue, net income,
segment count, source method).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import OUTPUT_DIR, DATA_DIR, TICKER_SECTOR, EXCEL_PATH


def _excel_ticker_order() -> Dict[str, int]:
    """Return {ticker: row_index} from the Excel file to preserve original ordering."""
    try:
        import pandas as pd
        df = pd.read_excel(EXCEL_PATH, sheet_name="Valuation Data")
        df.columns = [c.strip() for c in df.columns]
        return {str(row.get("Ticker", "")).strip(): i
                for i, (_, row) in enumerate(df.iterrows())
                if str(row.get("Ticker", "")).strip()}
    except Exception:
        return {}

_SEGMENTS_DIR = DATA_DIR / "segments"


def _fmt_money(v):
    if v is None:
        return "—"
    a = abs(v); sign = "-" if v < 0 else ""
    if a >= 1e12: return f"{sign}${a/1e12:.2f}T"
    if a >= 1e9:  return f"{sign}${a/1e9:.2f}B"
    if a >= 1e6:  return f"{sign}${a/1e6:.1f}M"
    if a >= 1e3:  return f"{sign}${a/1e3:.0f}K"
    return f"{sign}${a:.0f}"


def _method_badge(m: str) -> str:
    colors = {
        "xbrl":      ("#28a745", "XBRL"),
        "edgar":     ("#17a2b8", "EDGAR"),
        "edgar+llm": ("#6f42c1", "EDGAR+LLM"),
        "yahoo+llm": ("#fd7e14", "Yahoo"),
        "llm":       ("#007bff", "LLM"),
    }
    bg, txt = colors.get(m or "", ("#6c757d", (m or "?").upper()))
    return (f'<span style="background:{bg};color:white;padding:1px 7px;'
            f'border-radius:8px;font-size:11px;font-weight:600">{txt}</span>')


def _load_snapshot(ticker: str) -> Optional[Dict]:
    """Pull key metrics from the cached aggregated JSON."""
    p = _SEGMENTS_DIR / f"{ticker}.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    latest = d.get("latest_annual") or {}
    segs   = latest.get("segments") or []
    return {
        "ticker":        d.get("ticker", ticker),
        "name":          d.get("name", ticker),
        "period":        latest.get("period", ""),
        "total_revenue": latest.get("total_revenue"),
        "net_income":    latest.get("net_income"),
        "operating_income": latest.get("operating_income"),
        "gross_profit":  latest.get("gross_profit"),
        "n_segments":    len(segs),
        "sector":        TICKER_SECTOR.get(ticker, "—"),
        "method":        latest.get("extraction_method", ""),
        "confidence":    latest.get("confidence", 0.0),
        "annual_count":  d.get("annual_count", 0),
        "quarterly_count": d.get("quarterly_count", 0),
        "ttm_revenue":   d.get("ttm_revenue"),
    }


def build_index(output_dir: Path = OUTPUT_DIR) -> Path:
    """Scan output_dir for *_sankey.html reports and write an index.html."""
    output_dir = Path(output_dir)
    files = sorted(output_dir.glob("*_sankey.html"))
    snapshots: List[Dict] = []
    for f in files:
        ticker = f.stem.replace("_sankey", "")
        snap = _load_snapshot(ticker)
        if snap is None:
            snap = {"ticker": ticker, "name": ticker, "period": "",
                    "total_revenue": None, "net_income": None,
                    "operating_income": None, "gross_profit": None,
                    "n_segments": 0, "top_segment": "", "method": "",
                    "confidence": 0.0, "annual_count": 0, "quarterly_count": 0,
                    "ttm_revenue": None}
        snap["file"] = f.name
        snapshots.append(snap)

    # Sort by Excel row order (preserves the original ranking in the spreadsheet)
    excel_order = _excel_ticker_order()
    snapshots.sort(key=lambda s: excel_order.get(s["ticker"], 9999))

    rows_html = ""
    for s in snapshots:
        op_margin = ""
        if s["total_revenue"] and s["operating_income"] is not None:
            op_margin = f"{s['operating_income']/s['total_revenue']*100:.1f}%"
        net_margin = ""
        if s["total_revenue"] and s["net_income"] is not None:
            net_margin = f"{s['net_income']/s['total_revenue']*100:.1f}%"
        rows_html += f"""
<tr data-ticker="{s['ticker']}" data-name="{s['name'].lower()}" data-sector="{s.get('sector','').lower()}">
  <td><strong><a href="{s['file']}" target="_blank">{s['ticker']}</a></strong></td>
  <td>{s['name']}</td>
  <td style="color:#555;font-size:12px">{s.get('sector','—')}</td>
  <td>{s['period']}</td>
  <td data-val="{s['total_revenue'] or 0}" style="text-align:right">{_fmt_money(s['total_revenue'])}</td>
  <td data-val="{s['net_income'] or 0}" style="text-align:right">{_fmt_money(s['net_income'])}</td>
  <td style="text-align:right;color:#666">{op_margin}</td>
  <td style="text-align:right;color:#666">{net_margin}</td>
  <td style="text-align:right">{s['n_segments']}</td>
  <td>{_method_badge(s['method'])}</td>
  <td style="text-align:right">{s['confidence']*100:.0f}%</td>
  <td style="text-align:center">{s['annual_count']}A / {s['quarterly_count']}Q</td>
  <td style="text-align:center"><a href="{s['file']}" target="_blank"
         style="background:#1a1a2e;color:white;padding:4px 12px;border-radius:4px;text-decoration:none;font-size:12px">Open →</a></td>
</tr>"""

    xbrl_n  = sum(1 for s in snapshots if "xbrl" in (s.get("method") or ""))
    llm_n   = sum(1 for s in snapshots if "llm"  in (s.get("method") or ""))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Financial Analysis Dashboard — {len(snapshots)} Companies</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          margin: 0; padding: 20px; background: #f5f5f5; color: #222; }}
  .container {{ max-width: 1800px; margin: 0 auto; }}
  .header {{ background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
             color: white; padding: 24px 28px; border-radius: 10px;
             margin-bottom: 20px; }}
  .header h1 {{ margin: 0 0 8px 0; font-size: 1.6rem; }}
  .header .subtitle {{ opacity: 0.75; font-size: 13px; }}
  .stats {{ display: flex; gap: 14px; margin-bottom: 20px; flex-wrap: wrap; }}
  .stat-card {{ flex: 1; min-width: 180px; background: white; padding: 14px 18px;
                border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }}
  .stat-card .lbl {{ font-size: 11px; color: #888; text-transform: uppercase;
                     letter-spacing: 0.5px; }}
  .stat-card .val {{ font-size: 1.5rem; font-weight: 700; color: #1a1a2e;
                     margin-top: 4px; }}
  .toolbar {{ background: white; padding: 12px 18px; border-radius: 8px;
              margin-bottom: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.08);
              display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }}
  .toolbar input {{ flex: 1; min-width: 220px; padding: 8px 12px; border: 1px solid #ccc;
                    border-radius: 6px; font-size: 14px; }}
  .toolbar .count {{ color: #666; font-size: 13px; }}
  table {{ width: 100%; border-collapse: collapse; background: white;
           border-radius: 8px; overflow: hidden; font-size: 13px;
           box-shadow: 0 1px 4px rgba(0,0,0,0.08); }}
  thead {{ background: #1a1a2e; color: white; }}
  th {{ padding: 10px 12px; text-align: left; font-weight: 600;
        cursor: pointer; user-select: none; position: sticky; top: 0; }}
  th:hover {{ background: #2a2a4e; }}
  th .arrow {{ font-size: 10px; margin-left: 4px; opacity: 0.5; }}
  td {{ padding: 9px 12px; border-bottom: 1px solid #eee; }}
  tr:hover td {{ background: #f7f9fc; }}
  tr.hidden {{ display: none; }}
  a {{ color: #1a73e8; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .footer {{ text-align: center; color: #aaa; font-size: 11px; margin-top: 24px; }}
</style>
</head>
<body>
<div class="container">

  <div class="header">
    <h1>📊 Financial Analysis Dashboard</h1>
    <div class="subtitle">
      Generated from SEC EDGAR (10-K / 10-Q / 20-F) + Yahoo Finance —
      XBRL structured extraction with LLM fallback for segment narratives.
    </div>
  </div>

  <div class="stats">
    <div class="stat-card"><div class="lbl">Companies</div><div class="val">{len(snapshots)}</div></div>
    <div class="stat-card"><div class="lbl">XBRL Extractions</div><div class="val">{xbrl_n}</div></div>
    <div class="stat-card"><div class="lbl">LLM-assisted</div><div class="val">{llm_n}</div></div>
  </div>

  <div class="toolbar">
    <input id="search" type="search" placeholder="Search by ticker, name or sector…" />
    <span class="count" id="count">{len(snapshots)} companies</span>
  </div>

  <table id="tbl">
    <thead><tr>
      <th onclick="sortBy(0,'s')">Ticker<span class="arrow">▲▼</span></th>
      <th onclick="sortBy(1,'s')">Company<span class="arrow">▲▼</span></th>
      <th onclick="sortBy(2,'s')">Sector<span class="arrow">▲▼</span></th>
      <th onclick="sortBy(3,'s')">Period<span class="arrow">▲▼</span></th>
      <th onclick="sortBy(4,'n')" style="text-align:right">Revenue<span class="arrow">▲▼</span></th>
      <th onclick="sortBy(5,'n')" style="text-align:right">Net Income<span class="arrow">▲▼</span></th>
      <th style="text-align:right">Op&nbsp;Margin</th>
      <th style="text-align:right">Net&nbsp;Margin</th>
      <th onclick="sortBy(8,'n')" style="text-align:right">#&nbsp;Segs<span class="arrow">▲▼</span></th>
      <th>Source</th>
      <th onclick="sortBy(10,'n')" style="text-align:right">Conf<span class="arrow">▲▼</span></th>
      <th style="text-align:center">Periods</th>
      <th></th>
    </tr></thead>
    <tbody>{rows_html}</tbody>
  </table>

  <div class="footer">
    Financial Analysis System — Task 1 &nbsp;•&nbsp; {len(snapshots)} reports generated
  </div>
</div>

<script>
var tbl = document.getElementById('tbl');
var tbody = tbl.querySelector('tbody');
var search = document.getElementById('search');
var countEl = document.getElementById('count');
var TOTAL = {len(snapshots)};

search.addEventListener('input', function() {{
  var q = this.value.trim().toLowerCase();
  var shown = 0;
  Array.from(tbody.rows).forEach(function(r) {{
    var match = !q
      || (r.dataset.ticker || '').toLowerCase().includes(q)
      || (r.dataset.name || '').includes(q)
      || (r.dataset.sector || '').includes(q);
    r.classList.toggle('hidden', !match);
    if (match) shown++;
  }});
  countEl.textContent = shown + ' of ' + TOTAL + ' companies';
}});

var lastSort = {{ col: -1, dir: 1 }};
function sortBy(col, type) {{
  var rows = Array.from(tbody.rows);
  var dir  = (lastSort.col === col) ? -lastSort.dir : 1;
  lastSort = {{ col: col, dir: dir }};
  rows.sort(function(a, b) {{
    var ca = a.cells[col], cb = b.cells[col];
    if (type === 'n') {{
      var va = parseFloat(ca.dataset.val || ca.textContent.replace(/[^\\d\\.\\-]/g,'')) || 0;
      var vb = parseFloat(cb.dataset.val || cb.textContent.replace(/[^\\d\\.\\-]/g,'')) || 0;
      return (va - vb) * dir;
    }} else {{
      return ca.textContent.localeCompare(cb.textContent) * dir;
    }}
  }});
  rows.forEach(function(r) {{ tbody.appendChild(r); }});
}}
</script>
</body>
</html>"""

    out = output_dir / "index.html"
    out.write_text(html, encoding="utf-8")
    return out
