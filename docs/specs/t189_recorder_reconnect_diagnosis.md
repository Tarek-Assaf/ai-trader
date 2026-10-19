# T-189: Diagnose the recorder reconnect storm and measure missing minutes (read-only)

## Observation (owner health output, new Pod 1c60188b201d, image 1bdd99b, 2026-10-19)
`reconnect_count` rose 0 -> 9 in about 30 minutes (roughly one every 3 minutes), every time
`ConnectionClosedError`; recorder status flipped from healthy to degraded; 2 rejected candles
(`ask_close_below_bid_close`). Ticks and closed bars keep arriving. Only one Pod runs with these
Capital.com demo credentials (owner, 16:51 Riyadh). The earlier Pod today showed reconnect_count 0
in its first health output.

## Build (read-only; no broker order, no redeploy, no secrets in logs)
1. Read stream.py and the recorder worker. Write down, from the code, every path that ends in a
   reconnect (ping timeout, protocol error, socket close, token refresh) and what is logged for each.
2. Add one INFO log line per reconnect with: the exception class, the close code and reason if
   present, seconds since the last ping reply, seconds since the last tick, and the connection age.
   No tokens, no credentials. Unit test with a fake socket for each path.
3. Check ping handling: Capital.com expects a ping at least every 10 minutes and the stream token
   expires on inactivity; confirm the ping interval, that the ping reply is awaited correctly and that
   a ping does not race with a message read on the same socket.
4. Tool `scripts/lab_t189_minute_gaps.py` (read-only): read the recorder lake 1m rows for the Tai epic
   for a given UTC window and report missing minutes while the market was open (use MarketHours),
   rows with only bid or only ask, and rejected candles. Output a short markdown table under
   docs/evidence/t189/. Unit test with a temporary lake.
5. Hypotheses to confirm or rule out, each with the evidence: (a) another session using the same demo
   login; (b) the client sends a ping or subscribe message the server answers with a close;
   (c) the reconnect itself triggers the next close (token reuse after reconnect);
   (d) an idle or message-size limit; (e) a network issue on the Pod.
   If a fix is obvious and small (for example ping timing), implement it with a test; otherwise
   report the cause and a recommended fix, do not guess.

## Rules
No order or broker write call, no redeploy, no risk or gate edits, no secrets in code, logs or evidence.
Hand back to claude with the cause (or the best-supported hypothesis), the gap numbers, and the Pod
steps the owner must run, if any.

## Acceptance
Reconnect paths documented with log lines; gap tool and tests; a written conclusion naming the most
likely cause with evidence; CI green (ruff 0.9.6 line 100, mypy, pytest). One log entry, hand to claude.
