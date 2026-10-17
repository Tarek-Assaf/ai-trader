# T-146 per-group checkpoint evidence

These eight JSON files are the repository-local copies of the bounded T-153 group checkpoints. The complete 60m groups retain their 18 registered result rows; the six groups stopped at the 1200-second guard retain the progress and timeout metadata.

## Why `library_windows` is zero

The accepted runner returned zero eligible continuous in-sample windows for every group. The Pod lake file listing had no `NSXUSD/1m` parquet source (it showed `NAS100/1m_capital` and `XAUUSD/1m` data). XAUUSD had 211 monthly 1m files, but the T-146 window builder requires contiguous bars and produced no eligible 30, 60 or 120 bar windows. The 60m `NOT_FOUND` rows therefore describe missing usable windows, not an absence of a trading edge.

## Why timed groups show 36 queries

The runner invokes the accepted study with `max_queries=2`. There are 18 registered length/method/horizon rows (three lengths, two methods and three horizons), so each timed group records 18 x 2 = 36 query evaluations before the 1200-second guard. The partial groups emitted no complete result rows; their timeout metadata is preserved in the corresponding JSON.

No broker calls, orders, holdout reads, gate, risk, cost, ledger or promotion paths were used.
