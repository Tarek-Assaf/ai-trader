# T-210: Pod inventory against the architecture, and what is still missing (read-only)

## Why
Owner question (2026-10-20): is everything on the Pod running as designed, and what is missing? We know Postgres, Redis, the API
and the recorder/scheduler run (role=all) and that health is degraded (T-208). We have not checked the rest of ARCHITECTURE.md.

## Do (read-only on the Pod; no restart, config, code or orders)
1. List every process and supervisord program on the Pod with status, uptime, restart count, and the last log line. Include
   whether each of these is running/enabled/reachable, using the Pod's own config and logs (not assumptions):
   Telegram bot and command handlers, scheduled daily/weekly/monthly reports, backup job to GitHub and HuggingFace (last success of
   each), Tai demo runner, kill/pause controls, Risk Guardian and Order Manager wiring, LLM client (provider, mock or real, spend cap),
   Researcher/Analyst/Strategist/Critic graph (invoked in the runtime or only in tests), news/macro/calendar feeds (keys present yes/no,
   never values), scheduler jobs, autoscaler (enabled, thresholds, RUNPOD key present yes/no), data recorder for NSXUSD and XAUUSD.
2. For each ARCHITECTURE.md section 3, 5.2, 5.9-5.12 and 8 component, mark: running as designed / running but degraded / built but not
   wired or not enabled / not built. Give the evidence (process, log line, file) per row.
3. Check env flags by name only: which required variables are set (yes/no) and which are missing.
4. Note XAUUSD: is it recorded and is any strategy trading it.

## Rules
Read-only. Never print secret values. No restart, no config, no orders.

## Acceptance
`docs/evidence/t210/report.md` with the table, `summary.json`; one log entry; turn to claude; stop.
