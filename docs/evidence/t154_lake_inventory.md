# T-154 lake inventory

The applied Pod image has both expected symbol directories: XAUUSD (211 parquet
files, 6,140,217 rows) and NSXUSD (191 files, 5,285,568 rows). The timestamp
column in the sampled files is `timestamp[us, tz=UTC]`.

The XAUUSD sample `2009/03.parquet` has 16,400 rows. It contains 16,086 exact
one-minute steps (98.09%); 313 steps are longer than one minute, including two
2,881-minute weekend-sized gaps. The NSXUSD sample `2010/12.parquet` has 26,165
rows and 23,521 exact one-minute steps (89.90%); 2,643 steps are longer than one
minute, including four longer than 60 minutes.

These results show that a strict adjacent-timestamp rule will reject windows
that cross closures, but they do not yet prove whether shorter gaps also break
otherwise valid in-session windows. The next diagnostic is a bounded per-file
window-failure sample, followed by the session-aware continuity fix and tests.

The checks were read-only. No broker calls, orders, holdout reads, gate changes,
cost calculations, ledger writes, or promotion activity occurred.
