# T-199 Pod replay evidence

## Run

The bounded read-only replay was run through the reachable owner Pod terminal at
`https://pbkmi8nx193x3w-19123.proxy.runpod.net/gt00f7em302mcpp6gezy1e5nzxyeph5r/`.
The command was:

```text
timeout 180s python /app/lab_t195_runtime_miss.py --lake /workspace/data/lake --holdout-start 2026-07-01T00:00:00Z --epic NSXUSD --days 10 --output-dir /workspace/t199_run
```

The run completed with `status=ok` and the safety counters all zero. It did not
call a broker, place orders, read the sealed holdout, write the lake, redeploy,
or access secrets.

## Result

The port contains 26 entry units and the bounded runtime produced 1 entry. The
comparison is therefore **1/26**, with no extras. The one matching entry is
`2026-06-24T16:00:00+00:00 BUY` (entry `29556.867`, stop
`29462.963656558964`). The other 25 rows are classified as
`runtime_gate_ha_cross`; two rows additionally expose a bar/stop mismatch. This
names the remaining first differing quantity rather than claiming the 22/26
acceptance threshold.

The replay's first three entry-bar EMA200 values are recorded below. Values are
the closed-port value, bounded runtime value, unbounded runtime value, and
bounded-minus-port delta respectively.

| Entry bar (UTC) | Side | Port EMA200 | Runtime EMA200 | Unbounded EMA200 | Delta |
| --- | --- | ---: | ---: | ---: | ---: |
| 2026-06-19T08:21:00Z | BUY | 30241.5510512159 | 30241.288876608574 | 30241.288876608574 | -0.2621746073234234 |
| 2026-06-19T11:57:00Z | SELL | 30272.2985023102 | 30272.13913103505 | 30272.17088889924 | -0.1593712750727182 |
| 2026-06-21T23:30:00Z | SELL | 30312.022663035947 | 30311.62496894538 | 30311.98788472692 | -0.3976940905668016 |

The exact machine-readable details, including all 26 port rows and the safety
object, are in `summary.json`. Since the replay still misses 25 rows, T-199
remains blocked on the acceptance criterion; the Pod URL blocker itself is
resolved.
