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


## Q-024 resolution and lake QA (2026-10-04)

The 360-row difference is exact duplicate data, not rejected or invalid prices. The per-month lineage QA reports account for all 360 rows for each symbol: 60 exact duplicate timestamp-and-OHLCV rows in each October archive from 2019 through 2024. The importer retained one copy of each timestamp. Both symbols had zero conflicting duplicates and zero invalid rows. The rerun read and wrote zero rows for both symbols.

Whole-lake Parquet QA on `/workspace/data/lake`:

| Symbol | Monthly files | Covered months | Stored rows | Duplicate timestamps | Adjacent month range overlaps |
|---|---:|---|---:|---:|---:|
| NSXUSD | 191 | 2010-11 through 2026-09 | 5,285,557 | 0 | 0 |
| XAUUSD | 211 | 2009-03 through 2026-09 | 6,140,206 | 0 | 0 |

The month-file counts cover every calendar month in each stated range. NSXUSD begins in November 2010 because HistData reported January–October 2010 unavailable. Timestamp uniqueness was checked within every Parquet file; adjacent month minimum/maximum ranges were also compared, so no duplicate timestamp can span adjacent partitions.

Gap QA counted timestamp differences greater than 60 seconds, both within monthly files and across each adjacent-file seam. The totals are raw data gaps and are not classified against trading sessions or market holidays; many include expected market closures.

| Symbol | Within-file gap intervals | Within-file absent minutes | Month-seam gap intervals | Month-seam absent minutes | Total intervals | Total absent minutes |
|---|---:|---:|---:|---:|---:|---:|
| NSXUSD | 114,664 | 2,852,691 | 89 | 203,791 | 114,753 | 3,056,482 |
| XAUUSD | 35,585 | 2,859,314 | 83 | 219,540 | 35,668 | 3,078,854 |

Here “absent minutes” is the sum of whole one-minute intervals between consecutive stored timestamps, excluding the observed endpoints. These totals are descriptive QA, not findings that every absent minute should have a bar. No Pod data was changed by the QA.
