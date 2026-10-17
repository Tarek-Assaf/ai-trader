# Basis and spread calibration

The deterministic report writer is implemented in `trader.data.calibration` and
must be run after the Capital.com recorder has collected an overlapping window.
The one-command runner is:

```text
python -m trader.data.calibration --histdata <dir> --recorder <dir> \
  --instrument NAS100 --report <md path> --cost-model <yaml path>
```

The current repository contains the August 2026 HistData bid imports, but no
Capital.com recorder Parquet window, so no empirical basis or spread number is
claimed here. Running the report with recorder output produces instrument and
session tables containing n, median, p90 and p99 for basis, HistData spread and
Capital.com spread, plus the ±6 hour return-lag timezone check. The cost-model
writer records input paths, SHA-256 hashes, row counts, generation time and the
Git commit, and refuses to write until each session meets its minimum sample
guard. Paper execution costs remain provisional until that run is completed on
the Pod.
