# T-189 minute-gap report

The owner must run the read-only command below against the recorder lake and a
saved Capital.com market-details response. The command writes this report with
the actual window counts and timestamps; it does not contact the broker or
modify the lake.

```text
python scripts/lab_t189_minute_gaps.py \
  --lake-root data/lake \
  --market-details-json /path/to/market-details.json \
  --status-path data/recorder-status.json \
  --epic NSXUSD \
  --start 2026-10-19T00:00:00Z \
  --end 2026-10-20T00:00:00Z \
  --out docs/evidence/t189/minute_gap_report.md
```

This checkout does not contain the owner's Pod lake or market-details payload,
so no fabricated gap count is recorded here. The unit test uses a temporary
three-minute fixture and covers a paired minute, a bid-only minute, and a
missing minute classified through `MarketHours`.
