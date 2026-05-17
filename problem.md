## Known limitations (structural — cannot be fixed further)

### Group 1 — Yahoo only, no segment breakdown (conf=0.55)

**Affected:** TCEHY, 005930.KS, 000660.KS, ALV.DE, MC.PA, NESN.SW, RHHBY, SIE.DE, CBA.AX

Yahoo Finance only provides aggregate income statements without segment breakdown. Not fixable — data source limitation.

- TCEHY, 005930.KS, 000660.KS: no SEC filing, Yahoo is the only source
- CBA.AX, NESN.SW, RHHBY: half-yearly reporting — Yahoo returns 0 quarterly income entries
- ALV.DE, MC.PA, SIE.DE: INTL_YAHOO — no 20-F filed with SEC

### Group 2 — Single segment (no breakdown available)

**Affected:** CVX, NFLX, APP, BKNG, T

Verified: this is a real data limitation, not a pipeline bug.

- **NFLX, APP, BKNG:** company genuinely reports a single operating segment in its filing
- **T (AT&T):** single "Communications" segment after WarnerMedia spin-off — correct for current structure
- **CVX:** XBRL only exposes geographic axis; LLM returns aggregate "Reportable Segment"

### Group 3 — Quarterly coverage below 12 periods (structural)

**Affected:** all tickers

- **US companies:** 10–11 quarters — fiscal year-end Q4 is not in 10-Q filings; Yahoo only keeps ~5 recent quarters
- **INTL companies:** 5–6 quarters — 6-K filings have no structured income statement; all quarterly data comes from Yahoo

Each company report includes an LLM-generated note explaining the gap.
