# Basis and spread calibration

The deterministic report writer is implemented in `trader.data.calibration` and
must be run after the Capital.com recorder has collected an overlapping window.
The current repository contains the August 2026 HistData bid imports, but no
Capital.com recorder Parquet window, so no empirical basis or spread number is
claimed here. Running the report with the recorder output produces per-session
basis distributions, HistData and Capital.com spread median/p90/p99 values, and
the ±6 hour return-lag timezone check. Paper execution costs remain provisional
until that run is completed on the Pod.
