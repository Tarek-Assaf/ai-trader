# T-094 RunPod list response evidence

The autoscaler uses the RunPod REST base URL `https://api.runpod.io/v2` and
issues a read-only `GET /pods?status=RUNNING` before evaluating workers. The
adapter accepts the observed list envelope (`{"pods": [...]}`) and the
compatible `{"data": [...]}` envelope, validates every worker id, role,
status, tags, cost and CPU field, and never logs the bearer token. The unit
fixture in `tests/unit/test_scaling.py::test_runpod_adapter_matches_autoscaler_protocol`
verifies the list shape without network access.

No live RunPod request was made in this desktop session: no Pod/API credential
or owner-authorized external call was available. The owner must run one
names-only list check after deployment before enabling `scaling.enabled`; the
config remains `enabled: false` and the hourly cap remains owner-gated.
