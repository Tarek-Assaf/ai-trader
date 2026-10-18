# T-165 within-trade price-move alignment

**Verdict:** Within-trade moves do not align safely. The best 1m result is a +3 minute shift with 17/31 parent trades within the 25% or 10-point tolerance (25/31 sign matches, median absolute error 9.3833, permutation p=0.0005). This is below the 20/31 acceptance threshold, so no corrected historical Tai replay is authorized. Request a chart-bar CSV export or the Pine Inputs/Style screenshot.

This was one bounded, diagnostic-only offline Pod run. It made zero broker calls or orders, did not read the sealed holdout, and wrote no lake or production state.

## Method

The owner export contains 54 pyramided tranches and 31 distinct entry timestamps. The acceptance count is 31, so rows sharing an entry timestamp and direction were collapsed into parent trades using quantity-weighted entry/exit prices, the final exit timestamp, summed excursion dollars, and the longest duration. Owner times were parsed as America/New_York. NSXUSD 1m mid bars were scanned at every shift from -360 through +360 minutes with a fixed 2,000-permutation seed of 164. A move matches when its direction agrees and its absolute error is at most max(25% of the owner move, 10 points).

## Results

| Check | Best shift | Samples | Sign matches | Tolerance matches | Median absolute error |
|---|---:|---:|---:|---:|---:|
| NSXUSD 1m | +3 min | 30 | 25/31 | 17/31 | 9.3833 |
| 3m owner-as-open | +3 min | 29 | 23/31 | 12/31 | 13.3500 |
| 3m owner-as-close | +6 min | 29 | 23/31 | 12/31 | 13.3500 |

The p-value is significant for the best shift, but the match count is not high enough to claim alignment. Zero shift is not the best shift.

### Excursions

At shift zero, 21/30 available trades reached the owner favorable excursion within 25% tolerance and 15/30 stayed within the adverse excursion tolerance. At the best +3 minute shift, 30/30 reached favorable excursion and 20/30 stayed within adverse tolerance. The excursion result does not overcome the 17/31 move-match failure.

### Duration cross-check

There was one duration misfit: parent trade 1 elapsed 120 minutes while its reported duration was 20 three-minute bars (60 minutes).

### Coverage and safety

NSXUSD coverage was 12,350 1m, 4,013 3m, 2,355 5m and 118 60m bars. XAUUSD had no owner rows. Safety counters were broker_calls=0, orders=0, sealed_holdout_read=false, lake_writes=0, production_state_writes=0, gate_evaluated=false, tuning=false.

Pod artifact SHA-256 values are recorded in [t165_shifts.json](t165_shifts.json).
