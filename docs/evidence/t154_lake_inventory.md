# T-154 lake inventory

The applied Pod image has both expected symbol directories: XAUUSD (211 parquet
files, 6,140,217 rows) and NSXUSD (191 files, 5,285,568 rows). The timestamp
column in the sampled files is `timestamp[us, tz=UTC]`.

The XAUUSD sample `2009/03.parquet` has 16,400 rows. It contains 16,086 exact
one-minute steps (98.09%); 313 steps are longer than one minute, including two
2,881-minute weekend-sized gaps. The NSXUSD sample `2010/12.parquet` has 26,165
rows and 23,521 exact one-minute steps (89.90%); 2,643 steps are longer than one
minute, including four longer than 60 minutes.

These results show that a strict adjacent-timestamp rule rejects windows that
cross closures while preserving valid in-session windows. Direct bounded
checks over copied 2022-2024 files produced 46,415/31,084/29,277 XAUUSD
windows for lengths 30/60/120 and 44,947/30,160/28,368 NSXUSD windows. The
maximum contiguous XAUUSD 2024 run was 1,380 one-minute bars. For 60-minute
bars, the known HistData daily break from 20:00/21:00 to 23:00 UTC is treated
as skipped trading-time hours; weekend and unknown gaps remain hard boundaries.
Smaller timeframes keep strict adjacent spacing so they cannot bridge a
session closure.

The previous T-153 zero-window summary was a reporting defect: a timed-out
child had not written its final JSON, so the parent fallback hard-coded
`library_windows=0`. T-154 now records the window inventory in the child
heartbeat before matching starts and preserves it in partial checkpoints. The
loader also falls back to the legacy `NAS100/1m_capital` tree only when the
canonical `NSXUSD/1m` tree is absent. Unit coverage accepts in-session spacing,
handles the known hourly daily break, and rejects weekend and unknown session
gaps without filling data.

## Bounded post-fix window check

The copied 2025-2026 out-of-sample lake produced these window inventories under
the safe continuity rule (30/60/120 bars, followed by the query total):

| Group | 30 | 60 | 120 | Queries |
| --- | ---: | ---: | ---: | ---: |
| NSXUSD 1m | 17,693 | 16,051 | 15,162 | 48,906 |
| NSXUSD 3m | 11,169 | 9,534 | 3,914 | 24,617 |
| NSXUSD 5m | 12,652 | 5,039 | 1,717 | 19,408 |
| NSXUSD 60m | 0 | 0 | 0 | 0 |
| XAUUSD 1m | 18,450 | 16,805 | 16,009 | 51,264 |
| XAUUSD 3m | 11,736 | 10,170 | 4,301 | 26,207 |
| XAUUSD 5m | 13,428 | 5,478 | 1,971 | 20,877 |
| XAUUSD 60m | 724 | 190 | 0 | 914 |

NSXUSD 60m remains stopped because its resampled source contains additional
same-day missing hours, including 11:00-13:00 and 20:00-23:00 UTC. The task
acceptance rule says to stop when a post-fix group still has zero library
windows, so T-154 does not write a matching or net-of-cost verdict. The
diagnostic report identifies the remaining source-data gap for owner review;
no arbitrary gap bridging was added.

The checks were read-only. No broker calls, orders, holdout reads, gate changes,
cost calculations, ledger writes, or promotion activity occurred.
