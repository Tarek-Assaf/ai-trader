# Q-024 evidence: HistData import on the pod (2026-10-04)

Image: ghcr.io/tarek-assaf/ai-trader:01cdc90570db73e53506239ca80964953e19e1aa (includes T-082 yearly datemonth fix).
Summaries are saved on the network volume: /workspace/i3_x.json, i3_g.json (first run), i4_x.json, i4_g.json (rerun).

| Symbol | Range requested | rows_read | rows_written | invalid_rows | rejected_months |
|---|---|---|---|---|---|
| NSXUSD | 2010-2026 | 4,694,602 | 4,694,242 | 0 | none |
| XAUUSD | 2009-2026 | 5,526,930 | 5,526,570 | 0 | none |

Rerun of both symbols: rows_read 0, rows_written 0, invalid_rows 0 (idempotent; every month already complete).
NSXUSD: 201001-201010 reported unavailable at HistData (history starts late 2010). 2025-2026 months were skipped in the XAUUSD run because they were imported earlier.

Open for Codex (Q-024 QA): explain the 360-row difference between rows_read and rows_written in each run (expected: duplicate or overlap rows at archive/month boundaries), and run row-count, gap and overlap QA on the lake. T-080 can be closed after that.
