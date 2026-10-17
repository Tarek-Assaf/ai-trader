# T-100 recorder health check

After redeploying the current image, inspect the redacted worker health response and
record only these fields:

```text
data_recorder.configured
data_recorder.status
data_recorder.market_status
data_recorder.market_status_source
data_recorder.expected_open
data_recorder.stale_epics
data_recorder.ping_reply_ok
data_recorder.last_update_at
data_recorder.last_closed_bar_at
data_recorder.reconnect_count
data_recorder.last_reconnect_reason
data_recorder.last_liveness_at
```

For a healthy tradeable stream, `status` is `healthy`, `ping_reply_ok` is `true`,
`stale_epics` is empty, and both update timestamps advance. If the stream cannot
connect or stops delivering events, `status` is `degraded`, `reconnect_count`
increases, and `last_reconnect_reason` contains only a short safe reason such as
`OSError`, `StreamProtocolError`, or `no_data_timeout`; credentials, URLs and raw
broker messages are never included.
