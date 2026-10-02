"""One-time generator for the seed AGENT_HANDOFF.json.

After the seed is created, agents edit AGENT_HANDOFF.json directly.
This script is kept only so the seed can be reproduced.
"""
import json
from pathlib import Path

NOW = "2026-10-02T00:00:00Z"

REQS = [
    ("R-01", "Natural-language interaction; validate a suitable chat platform", "Telegram primary, web UI optional"),
    ("R-02", "All trading analysis: indicators, technical analysis, news analysis", ""),
    ("R-03", "Learn, identify and validate strategies; find the most reliable and efficient", ""),
    ("R-04", "Detect profitable patterns, validate them, promote to strategies if suitable", ""),
    ("R-05", "Use HistData historical data to validate strategies", "HistData is M1, bid-only, EST without DST"),
    ("R-06", "Identify least-risky trades and execute them", "Trade quality score plus Risk Guardian"),
    ("R-07", "Know when markets are open and closed", ""),
    ("R-08", "Understand P&L, risk, lot sizing relative to account size", "Account size means account equity"),
    ("R-09", "Timeframes 1M, 3M, 5M; extensible", "Capital.com has no 3M resolution: resample from 1M"),
    ("R-10", "Capital.com integration; other brokers later", ""),
    ("R-11", "Paper trading / demo account first", ""),
    ("R-12", "Handle requests: research, graphs, stop trading; act as kill switch", "Stop path must not depend on the LLM"),
    ("R-13", "Visual graphs for validation, analysis and sharing on request", ""),
    ("R-14", "Automatic backup of everything to GitHub and HuggingFace", "No secrets in backups"),
    ("R-15", "API integrations: Capital.com, Telegram, others", ""),
    ("R-16", "Integrate any resource to request, validate and identify data/searches", ""),
    ("R-17", "Self-research through APIs or web portals", ""),
    ("R-18", "Telegram alerts; daily, weekly, monthly reports via Telegram or email", ""),
    ("R-19", "Claude and Codex coordinate through a shared JSON loop", ""),
    ("R-20", "Record everything performed so agents avoid repeated discussions and validations", ""),
    ("R-21", "Fully autonomous agentic system within hard limits", ""),
    ("R-22", "Expandable; detects overload and adds workers", "Workers only; core services are singletons"),
    ("R-23", "Portable Docker container on RunPod, easy to duplicate", ""),
    ("R-24", "NAS100 and Gold now; more instruments later via config", ""),
    ("R-25", "Think, search, plan, and use tools, APIs and systems", ""),
]

# task: id, title, phase, owner, reviewer, requirements, depends_on, acceptance
T = []


def t(id_, title, phase, owner, reqs, deps, acc, reviewer=None):
    if reviewer is None:
        reviewer = "claude" if owner == "codex" else "codex"
    T.append(dict(
        id=id_, title=title, phase=phase, owner=owner, reviewer=reviewer,
        status="todo", requirements=reqs, depends_on=deps,
        acceptance_criteria=acc, deliverables=[], review_cycles=0,
        evidence=[], completion_note="",
    ))


# ---- Claude specs first
t("C-001", "Write detailed Phase 0 spec (repo scaffold, Docker roles, config loader, DB, CI)", 0, "claude",
  ["R-19", "R-23"], [], ["docs/specs/phase0.md exists", "every Codex task in phase 0 has acceptance criteria"], "codex")
t("C-002", "Write default config/risk.yaml and config/strategy_lab.yaml with commented thresholds", 0, "claude",
  ["R-08", "R-21"], [], ["files exist with comments in plain English", "owner open decisions listed in questions[]"], "codex")
t("C-003", "Write specs for Phases 1 and 2 (data and risk/execution)", 1, "claude",
  ["R-05", "R-07", "R-08", "R-10"], ["C-001"], ["docs/specs/phase1.md and phase2.md exist"], "codex")

# ---- Phase 0
t("T-001", "Repo scaffold: pyproject, Makefile, ruff, mypy, pytest, folder layout from ARCHITECTURE.md section 11", 0, "codex",
  ["R-23"], ["C-001"], ["make lint test passes on empty skeleton", "folder layout matches ARCHITECTURE.md"])
t("T-002", "Config system: pydantic-settings loaders for all config/*.yaml, fail-fast validation, read-only config guard", 0, "codex",
  ["R-24", "R-21"], ["T-001"], ["invalid config stops startup with a clear error", "unit tests cover each config file"])
t("T-003", "Dockerfile and entrypoint with ROLE=all|core|worker|backtest, supervisord configs, /health", 0, "codex",
  ["R-23", "R-22"], ["T-001"], ["image builds", "ROLE=all starts api plus placeholder services", "non-root user"])
t("T-004", "Postgres schema and Alembic migrations (cycles, decisions, intents, orders, fills, positions, snapshots, lessons, audit_log, worker_heartbeats)", 0, "codex",
  ["R-20"], ["T-001"], ["migrations apply on empty DB", "audit_log is append-only (no update/delete grants or trigger)"])
t("T-005", "CI: lint, types, tests, docker build, secret scan, handoff validation", 0, "codex",
  ["R-19", "R-14"], ["T-001"], ["CI fails if validate_handoff.py fails", "CI fails on a planted fake secret"])
t("T-006", "infra/runpod guide for a non-programmer: create network volume, pod, secrets, deploy ROLE=all", 0, "codex",
  ["R-23"], ["T-003"], ["step-by-step guide with no assumed programming knowledge"])

# ---- Phase 1
t("T-010", "Capital.com REST client: session manager (CST/X-SECURITY-TOKEN), rate limiter (10 rps, 1 session/s), demo/live hosts, market search, market details, price history with pagination", 1, "codex",
  ["R-10", "R-15"], ["T-002", "C-003"], ["works against demo with real key (owner-supplied)", "rate limiter unit-tested", "tokens never logged"])
t("T-011", "Capital.com WebSocket client: OHLC bid/ask subscription, 10-minute ping, auto-reconnect", 1, "codex",
  ["R-10"], ["T-010"], ["reconnect survives forced disconnect test", "ping loop verified"])
t("T-012", "HistData importer: download, unzip, parse, EST(UTC-5 no DST) to UTC, QA, Parquet write; confirm NSXUSD and XAUUSD coverage dates", 1, "codex",
  ["R-05"], ["T-002", "C-003"], ["timezone test around US DST changes", "QA report per symbol", "coverage dates recorded in validations[]"])
t("T-013", "Resampler 1M to 3M/5M/any, shared by historical and live paths", 1, "codex",
  ["R-09"], ["T-012"], ["identical output for same minutes from either path", "gap bars flagged"])
t("T-014", "Own data recorder: continuous Capital.com 1M bid/ask and spread to Parquet", 1, "codex",
  ["R-05", "R-09"], ["T-011", "T-013"], ["no gaps across a reconnect", "lineage tag set"])
t("T-015", "Market hours service using broker market status, config sessions, holiday calendar, stale-tick check", 1, "codex",
  ["R-07"], ["T-010"], ["confirm broker market-status field/values and record in validations[]", "closed/break/holiday tests"])
t("T-016", "instruments.yaml and mapper (NAS100/US100/NSXUSD, XAUUSD/GOLD); size/margin read from broker details", 1, "codex",
  ["R-24"], ["T-010"], ["adding an instrument needs config only"])
t("T-017", "Basis measurement: HistData vs Capital.com on overlapping windows; spread model calibration", 1, "codex",
  ["R-05"], ["T-012", "T-014"], ["report with basis and spread stats per instrument/session"])

# ---- Phase 2
t("T-020", "Risk Guardian with all rules from ARCHITECTURE.md 5.3, property-based tests", 2, "codex",
  ["R-06", "R-08"], ["T-002", "C-002"], ["no input yields an order without stop or above limits", "each rule has tests"])
t("T-021", "Position sizing with broker size steps, min/max size, margin and leverage checks", 2, "codex",
  ["R-08"], ["T-016"], ["rounds down to size step", "rejects if min size exceeds risk budget"])
t("T-022", "Trade quality score (deterministic) and eligibility threshold", 2, "codex",
  ["R-06"], ["T-020"], ["score components documented and unit-tested"])
t("T-023", "PaperBroker with spread/slippage model and bracket orders", 2, "codex",
  ["R-11"], ["T-017"], ["BrokerAdapter contract tests pass"])
t("T-024", "CapitalComBroker adapter (demo first): place/modify/close with server-side stop, polling of positions and activity", 2, "codex",
  ["R-10", "R-11"], ["T-010", "T-020"], ["demo order round trip with stop attached", "contract tests pass"])
t("T-025", "P&L engine: realized/unrealized, R-multiple, equity, drawdown, reconciled to broker account", 2, "codex",
  ["R-08"], ["T-023"], ["reconciles with demo account figures"])
t("T-026", "Reconciler and order manager with idempotency keys and Redis leader lock", 2, "codex",
  ["R-21"], ["T-024"], ["duplicate order impossible in chaos test", "mismatch pauses entries and alerts"])
t("T-027", "Kill switch and pause (file flag, Redis key, API), deterministic and LLM-free", 2, "codex",
  ["R-12"], ["T-026"], ["kill takes effect before next order", "works with LLM provider unreachable"])

# ---- Phase 3
t("T-030", "Telegram channel: owner allow-list, commands, alerts, inline buttons", 3, "codex",
  ["R-01", "R-18"], ["T-027"], ["non-owner chats ignored", "all commands tested"])
t("T-031", "Chat router: deterministic fast path for stop/kill/status plus LLM intent path", 3, "codex",
  ["R-12", "R-01"], ["T-030"], ["natural-language stop phrases halt trading without calling the LLM", "dangerous requests refused"])
t("T-032", "Visualization module: candles with trades, equity, drawdown, heatmaps, strategy comparison; PNG and HTML", 3, "codex",
  ["R-13"], ["T-025"], ["charts generated from fixtures", "chart request via chat returns an image"])
t("T-033", "Reports: daily, weekly, monthly via Telegram and email", 3, "codex",
  ["R-18"], ["T-032"], ["sample reports rendered from fixtures", "email delivery configurable"])

# ---- Phase 4
t("T-040", "Analysis engine: indicators, levels, patterns, regime, multi-timeframe snapshot", 4, "codex",
  ["R-02"], ["T-013"], ["snapshot for 1M/3M/5M builds from recorded data", "indicator tests against reference values"])
t("T-041", "Research sources and sanitizer: web search, fetch, RSS, FRED, calendar, COT; cache and rate limits", 4, "codex",
  ["R-16", "R-17"], ["T-002"], ["prompt-injection fixtures produce no tool call", "provenance stored"])
t("T-042", "LLMClient interface with Anthropic/OpenAI/compatible adapters and MockLLM; token/spend budgets", 4, "codex",
  ["R-25"], ["T-002"], ["full pipeline runs offline with MockLLM"])
t("T-043", "Agent graph: Researcher, Analyst, Strategist, Critic, Orchestrator, tool registry with permissions", 4, "codex",
  ["R-21", "R-25"], ["T-040", "T-041", "T-042", "T-020"], ["end-to-end paper cycle with full audit trail", "agents cannot import broker code (test)"])
t("C-040", "Write agent prompts and tool permission table", 4, "claude",
  ["R-21", "R-25"], ["C-003"], ["prompts versioned in src/trader/agents/prompts/"], "codex")

# ---- Phase 5
t("T-050", "Backtest engine with calibrated costs, walk-forward, sealed holdout reader", 5, "codex",
  ["R-03", "R-05"], ["T-013", "T-017"], ["known-result test reproduces", "cost stress test supported"])
t("T-051", "StrategySpec schema and sandbox engine (no arbitrary code)", 5, "codex",
  ["R-03"], ["T-040"], ["invalid or unsafe spec rejected"])
t("T-052", "Validator, ledger, promotion gate, decay monitor, allocator", 5, "codex",
  ["R-03", "R-21"], ["T-050", "T-051", "C-002"], ["random-noise strategy rejected in repeated trials", "holdout unreadable by anything else"])
t("T-053", "Pattern miner: candidate generation and statistical screening feeding the same gates", 5, "codex",
  ["R-04"], ["T-052"], ["patterns promoted only through gates", "multiple-testing counted in ledger"])
t("T-054", "Idea generator and post-mortem agents", 5, "codex",
  ["R-03", "R-04"], ["T-052", "T-042"], ["Lab runs end to end on paper"])

# ---- Phase 6 / 7
t("T-060", "Backup job: GitHub (code/config/state) and HuggingFace (data/results/encrypted dumps), secret scan, restore drill", 6, "codex",
  ["R-14"], ["T-005"], ["fresh pod restored from backups", "push aborts on planted secret"])
t("T-070", "Queue, workers and autoscaler using RunPod API; priority shedding; singleton protection", 7, "codex",
  ["R-22"], ["T-003", "T-026"], ["load test scales up and down", "singletons never scaled"])

# review tasks for Claude
t("C-100", "Review Phase 0 deliverables against specs and write validations", 0, "claude",
  ["R-19", "R-23"], ["T-001", "T-002", "T-003", "T-004", "T-005"], ["validations[] updated with pass/fail and evidence"], "codex")

DATA = {
    "meta": {
        "project": "ai-trader",
        "schema_version": "1.0",
        "created": NOW,
        "description": "Single source of truth for coordination between Claude and Codex while building the trading agent.",
    },
    "turn": "codex",
    "revision": 1,
    "current_phase": 0,
    "protocol": {
        "writer_rule": "Only the agent named in 'turn' may edit this file. When finished, set 'turn' to the other agent and increment 'revision'.",
        "read_first": ["decisions", "validations", "blockers", "questions"],
        "log_rule": "log[] is append-only. Never edit or delete earlier entries.",
        "anti_loop": {
            "max_review_cycles_per_task": 2,
            "on_exceeding": "Add a question for the human with options and a recommendation; do not continue the argument.",
            "final_decisions_are_not_reopened": True,
            "passed_validations_are_not_repeated_unless_reverify_if_is_true": True,
        },
        "definition_of_done": ["tests pass", "acceptance criteria met", "evidence recorded", "completion_note written", "log entry added"],
        "human_override": "Owner may set turn to 'human' to pause both agents.",
        "commit_rule": "Commit messages start with the task id, for example 'T-010: Capital.com REST client'.",
    },
    "project_state": {
        "summary": "Blueprint v3 written. Nothing implemented yet. Next: Codex builds Phase 0 skeleton from C-001 spec once Claude publishes it; Claude must publish docs/specs/phase0.md first.",
        "how_to_run_tests": "make lint test (defined in T-001)",
        "known_failing": [],
        "environment": {"runtime": "RunPod Docker", "python": "3.12", "trading_mode": "paper", "broker_env": "demo"},
    },
    "requirements": [
        {"id": i, "text": txt, "notes": n, "status": "not_started",
         "covered_by": [x["id"] for x in T if i in x["requirements"]]}
        for i, txt, n in REQS
    ],
    "tasks": T,
    "decisions": [
        {"id": "D-001", "statement": "The LLM never has broker access. Proposals go through the deterministic Risk Guardian, then the Execution Service.", "status": "final", "decided_by": "architecture", "date": NOW, "do_not_reopen": True},
        {"id": "D-002", "statement": "Stop/pause/kill must work without the LLM (deterministic fast path).", "status": "final", "decided_by": "architecture", "date": NOW, "do_not_reopen": True},
        {"id": "D-003", "statement": "3M candles are produced by resampling 1M because Capital.com has no 3-minute resolution.", "status": "final", "decided_by": "claude", "date": NOW, "do_not_reopen": True},
        {"id": "D-004", "statement": "HistData is used for hypothesis generation; Capital.com demo forward testing is used for confirmation. HistData is bid-only, M1, EST without DST; convert to UTC on import.", "status": "final", "decided_by": "claude", "date": NOW, "do_not_reopen": True},
        {"id": "D-005", "statement": "Start with one RunPod pod (ROLE=all, CPU only); split into core and worker, and move to managed Postgres/Redis, before live trading.", "status": "final", "decided_by": "owner", "date": NOW, "do_not_reopen": True},
        {"id": "D-006", "statement": "Telegram is the primary natural-language channel; the web UI is optional and sits behind the same ChatChannel interface.", "status": "final", "decided_by": "claude", "date": NOW, "do_not_reopen": False},
        {"id": "D-007", "statement": "Strategies are declarative specs run in a sandbox; no LLM-written executable code runs in core or execution processes.", "status": "final", "decided_by": "architecture", "date": NOW, "do_not_reopen": True},
        {"id": "D-008", "statement": "Backups: GitHub for code/config/state, HuggingFace for data and results. Never back up secrets.", "status": "final", "decided_by": "owner", "date": NOW, "do_not_reopen": True},
        {"id": "D-009", "statement": "Account size for risk sizing means account equity (balance plus open P&L).", "status": "final", "decided_by": "claude", "date": NOW, "do_not_reopen": False},
    ],
    "validations": [
        {"id": "V-001", "subject": "Capital.com price resolutions exclude 3 minutes", "method": "Read public API docs and client library docs", "result": "pass", "evidence": "ARCHITECTURE.md section 2", "validated_by": "claude", "date": NOW, "reverify_if": "Capital.com publishes a new API version"},
        {"id": "V-002", "subject": "Capital.com rate limits: 10 req/s, 1 session/s, demo 1000 position requests/hour, WebSocket 10 min and 40 instruments", "method": "Read public API general information page", "result": "pass", "evidence": "ARCHITECTURE.md section 2", "validated_by": "claude", "date": NOW, "reverify_if": "429 errors seen at lower rates than documented"},
        {"id": "V-003", "subject": "HistData provides M1 and tick only, bid-based bars, EST without DST", "method": "Read HistData FAQ and file specification", "result": "pass", "evidence": "ARCHITECTURE.md section 2", "validated_by": "claude", "date": NOW, "reverify_if": "Imported timestamps look offset by a market-open mismatch"},
        {"id": "V-004", "subject": "HistData coverage dates for Nasdaq-100 (NSXUSD) and XAUUSD", "method": "Pending: Codex confirms during T-012", "result": "pending", "evidence": "", "validated_by": "", "date": "", "reverify_if": ""},
        {"id": "V-005", "subject": "Capital.com epics for NAS100/US100 and Gold, and market-status field values", "method": "Pending: Codex confirms during T-010 and T-015 against the demo API", "result": "pending", "evidence": "", "validated_by": "", "date": "", "reverify_if": ""},
    ],
    "blockers": [],
    "questions": [
        {"id": "Q-001", "to": "human", "from": "claude", "text": "Which risk limits do you want (risk per trade %, max daily loss %, max drawdown %, max concurrent live strategies)? Suggested starting point: 0.5%, 2%, 10%, 3.", "status": "open", "options": ["accept suggested defaults", "provide own numbers"], "answer": ""},
        {"id": "Q-002", "to": "human", "from": "claude", "text": "Do you have a Capital.com demo account with 2FA enabled and an API key generated?", "status": "open", "options": ["yes", "not yet"], "answer": ""},
        {"id": "Q-003", "to": "human", "from": "claude", "text": "How should reports be emailed (SMTP details or an email API such as Resend/SendGrid) and to which address?", "status": "open", "options": [], "answer": ""},
        {"id": "Q-004", "to": "human", "from": "claude", "text": "Names for the private GitHub repo and private HuggingFace dataset repo?", "status": "open", "options": [], "answer": ""},
    ],
    "log": [
        {"revision": 1, "ts": NOW, "agent": "claude", "action": "seed", "task_ids": [], "files_changed": ["ARCHITECTURE.md", "agent_state/AGENT_HANDOFF.json"],
         "summary": "Created blueprint v3 and seeded requirements, tasks, decisions, validations and open questions."},
    ],
}

# first turn must be Claude to publish specs C-001..C-003
DATA["turn"] = "claude"

if __name__ == "__main__":
    out = Path(__file__).with_name("AGENT_HANDOFF.json")
    out.write_text(json.dumps(DATA, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", out, "tasks:", len(T))
