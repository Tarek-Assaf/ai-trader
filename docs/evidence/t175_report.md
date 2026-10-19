# T-175 owner-exit-driven refresh diagnostic

Verdict: **not_reproduced** (7/31 owner entries reproduced; threshold 25).
Best mode: closed.

## Counts

Owner entries: 31; owner exits: 54; chart bars: 4178.
- closed: 7/31 overall; first=0; refresh=7
- forming: 7/31 overall; first=0; refresh=7

## Interpretation

First entries require a same-direction HA cross; refresh entries use the Pine refresh rule after owner exits. Every condition is reported at offsets -2..+2. Results below 15 should be treated as noise; if <=8, next causes are NQ1 futures versus NSXUSD CFD or Pine version differences.

## Safety

Replay only; no broker calls, orders, holdout reads, lake writes, tuning, or secrets.
