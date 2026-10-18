# T-156 raw entry feature dump

Diagnostic only. The run used the recorded NAS100 1m Capital.com lake and the owner CSV. It made zero broker calls, placed zero orders, did not read the sealed holdout, and wrote no gate, cost, ledger, or promotion state.

The Pod run produced 31 entry rows, each carrying offsets -3 through +3 in `window_json` (217 window observations). The committed CSV is a compact center-bar index for review; the executable diagnostic in `scripts/lab_t156_feature_dump.py` regenerates the complete window JSON and all raw values. Pod artifact checksums were:

* full CSV: `d0d64b01df51c68d575bda9681f60befff695103794f6a44322f32a6ab4e39bf`
* report: `9662f2ebcc4ec1bee25f9607c5b69e43d6a3805e3855027bb29eb5cd01796173`

## Coverage around entries

The ±9 minute 1m windows had 11 missing minutes, 5 zero-range minutes, and 5 flat-filled minutes in total. Eight 3m bars across the windows were built from fewer than three recorded minutes.

## Condition shortfalls at the entry bar

| Measure | Median | Positive samples | Samples |
|---|---:|---:|---:|
| ADX shortfall (points) | 1.0209 | 15 | 27 |
| DI shortfall (points) | 0.0000 | 13 | 27 |
| VWAP side shortfall (points) | 13.5302 | 14 | 27 |
| VWAP distance excess (ATR) | 1.3006 | 20 | 27 |
| Risk excess (ATR) | 0.0000 | 0 | 18 |

Positive directional margins were observed for EMA in 15/27 entries and DI in 14/27; VWAP side margin was positive in 13/27 and negative in 14/27. The dominant signs were therefore positive for EMA and DI, and negative for VWAP side.

## Bid, ask, and mid sensitivity

| Price basis | Entries with data | ADX + DI pass | VWAP side pass |
|---|---:|---:|---:|
| Mid | 27 | 6 | 13 |
| Bid | 27 | 6 | 13 |
| Ask | 27 | 6 | 13 |

No pass-count change was observed between bid, ask, and mid in this recorded window. The run cannot explain TradingView's BUY/SELL labels because the lake does not contain the TradingView script's internal state, alert timing, session settings, or any unrecorded feed. No parameter tuning was performed.
