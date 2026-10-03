# HistData automatic-download and coverage check

Checked 2026-10-03. Scope: August 2026 ASCII M1 archives for NSXUSD and XAUUSD.
This verifies the coverage of these two archives, not the full historical catalog.

The public download forms and ZIP responses were retrieved automatically,
without an account or manual upload. Archives are cached atomically, checked for
the requested symbol/month, ZIP integrity and size limits, and never extracted.
HistData's accompanying TXT status reports are excluded from price parsing.

| Symbol | Rows read | First UTC candle | Last UTC candle | Conflicting minutes | Rows written |
| --- | ---: | --- | --- | ---: | ---: |
| NSXUSD | 28,432 | 2026-08-02 23:00 | 2026-09-01 04:58 | 37 | 0 |
| XAUUSD | 29,437 | 2026-08-02 23:00 | 2026-09-01 04:58 | 98 | 0 |

Both archives have zero identical duplicate candles and zero invalid OHLC/volume
rows. The conflicts are repeated timestamps with differing candle values.
The importer rejects each conflicting source before writing any Parquet data.
Raw files and per-file QA JSON remain available locally. These M1 files remain
rejected. The tick reconstruction below supplies the imported candles instead.
Daily session gaps require the confirmed T-015 calendar.

## Validation against the underlying bid quotes

The owner requested further price validation, including public platforms where
useful. HistData's own tick exports are the source-matched reference: its
[format specification](https://www.histdata.com/f-a-q/data-files-detailed-specification/)
defines M1 as bid OHLC and tick files as timestamp, bid, ask, volume in fixed
UTC-5. Another provider's index CFD or Gold quote is not an exact oracle for
these bid prices; Capital.com basis/spread calibration remains T-017.

The NSXUSD tick archive contains 7,959,579 quotes and 2,199 timestamp reversals;
XAUUSD contains 5,213,048 quotes and 2,707 reversals. The M1 fragments are
consistent with minute aggregation being interrupted by out-of-order quotes.
That explanation is an inference from the files, not a claim about HistData's code.

Every affected minute was rebuilt from all of its bid quotes, ordered by timestamp.
Equal timestamps preserve the original archive sequence. Many quotes have only
second-level effective precision despite the millisecond-format timestamp, so
the archive sequence is explicitly recorded as the tie rule. High/low are the
extrema over every quote in the minute; open/close follow that stable order.

All 135 reconstructed conflict minutes match a separate reconstruction used
for the investigation. Only 1 of the 37 Nasdaq conflicts and 9 of the 98 Gold
conflicts had a single original candidate matching the complete tick candle.
Picking one of the duplicate M1 rows would therefore usually lose information.

Example: NSXUSD on 2026-08-03 at 14:31 UTC reconstructs to open 28,237.376,
high 28,261.075, low 28,215.921, close 28,227.198. One fragment had the correct
range but close 28,227.865; the other contained only the final bid 28,227.198.

| Rebuilt symbol | Valid UTC minute candles | Coverage UTC | Invalid rows | Conflicts |
| --- | ---: | --- | ---: | ---: |
| NSXUSD | 28,395 | 2026-08-02 23:00 to 2026-09-01 04:59 | 0 | 0 |
| XAUUSD | 29,340 | 2026-08-02 23:00 to 2026-09-01 04:59 | 0 | 0 |

The canonical bid candles were imported into the local Parquet lake. Source
hashes, quote counts, ordering statistics and coverage are in
`docs/reports/histdata_coverage.json`; full local QA remains in `data_lineage`.
This is source-feed validation, not independent exchange-price certification.

Archive SHA-256 checksums:

- `DAT_ASCII_NSXUSD_M1_202608.zip`: `b8d8cf3f1599ac2ae018883995df88ad552b790dd2a64c14c11d63b8883b8b8e`
- `DAT_ASCII_XAUUSD_M1_202608.zip`: `b6bdb6989098437df2df11cb2e0f16d145c138f66425b0848bd6ce575626fd77`

Download and import a completed month on the pod after merge and deployment:

```bash
cd /app
python scripts/import_histdata.py --download --from-ticks --symbol NSXUSD --year 2026 --month 8 \
  --raw /workspace/data/raw/histdata --lake /workspace/data/lake \
  --lineage /workspace/data_lineage
```

Repeat with `--symbol XAUUSD` for Gold. Omit `--from-ticks` to import a regular
M1 archive with strict duplicate rejection. Valid cached ZIPs are reused, and
existing valid timestamps are preserved on repeated imports. Empty responses
are never reported as success. Tick processing keeps one aggregate per minute,
avoiding loading all quotes into memory.


## Pod-side multi-year imports

T-080 adds year-range mode to `scripts/import_histdata.py`. It processes completed months serially, stops at the previous month, uses M1 archives when valid, and rebuilds an M1 month from raw ticks when its M1 archive has conflicting timestamps. It records rows, QA issues, gaps, rejected months, and each completed month in an atomic JSON manifest on the mounted pod volume.

The manifest seeds the four August archive checksums listed by this report and the tick reconstruction report. Hashes for later months are pinned on first download and checked on reuse. Those first-seen hashes provide cache integrity only; they do not independently attest the downloaded source. A mismatch fails closed for that month. Successful months are skipped on reruns after any cached archives for them are checked. Failed or rejected months remain eligible for retry. Use a one-second pause and run one symbol at a time on the same pod volume; do not run two importers concurrently.

Example (set the first year to the earliest one HistData offers for the symbol):

```sh
python scripts/import_histdata.py --symbol NSXUSD --from-year 2000 --through-year 2026 --pause-seconds 1 \
  --raw /workspace/data/raw/histdata --lake /workspace/data/lake \
  --lineage /workspace/data_lineage
```

Run again with `--symbol XAUUSD`. A `partial` status lists months that could not be fetched or passed QA and exits nonzero; inspect those months before claiming full coverage. The command prints a concise JSON summary without credentials.

Sources: [HistData NSXUSD August archive](https://www.histdata.com/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes/nsxusd/2026/8)
and [HistData XAUUSD August archive](https://www.histdata.com/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes/xauusd/2026/8).
