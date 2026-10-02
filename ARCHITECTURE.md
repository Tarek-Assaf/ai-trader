# Autonomous AI Trading Agent: Technical Blueprint (v3)

**Codename:** `ai-trader`
**Instruments (v1):** NAS100 / US100 (Nasdaq-100 CFD) and Gold (XAUUSD). Adding more must be a config change, not a code change.
**Timeframes (v1):** 1M, 3M, 5M. Extensible by config.
**Broker (v1):** Capital.com (demo first). Broker layer is an adapter so others can be added.
**Runtime:** Docker on RunPod, including live trading later.
**Builders:** Claude and OpenAI Codex, coordinating through `agent_state/AGENT_HANDOFF.json` (section 7).
**Status:** v3.1, agreed with the owner on 2026-10-02. Fresh build; the earlier project is reference material (`docs/legacy_audit.md`).

> Two different "AIs" appear in this project. Do not confuse them.
> - **Builder agents (Claude, Codex):** write the code. They exist only while the system is being built.
> - **Runtime agent (the trading AI):** runs inside the container and calls a hosted LLM API to think, research and decide.

---

## 0. Read This First (owner summary)

1. **Paper/demo first, always.** The system starts in paper mode and on the Capital.com **demo** account. Real money is only possible after the owner flips one switch (`LIVE_TRADING_ENABLED=true`) and a strategy has earned a live stage automatically (section 6.4).
2. **The LLM never touches the broker.** The AI proposes. A plain-code Risk Guardian approves or rejects and calculates the size. A separate Execution Service places the order. This is the main safety rule.
3. **The stop sign never depends on the AI.** `/pause`, `/kill` and natural-language "stop trading" are handled by a deterministic fast path that bypasses the LLM (section 5.9).
4. **Autonomous inside hard limits.** The agent discovers, tests and deploys strategies on its own, but cannot change risk limits, promotion gates or platform code.
5. **Honest expectation.** Many retail CFD accounts lose money, and 1M to 5M strategies are dominated by spread and slippage. The system may find nothing worth trading for long periods. "No qualified strategy, so no live trades" is a valid result.
6. **Start with one pod, no GPU.** Reasoning uses a hosted LLM API.

---

## 1. Requirements Traceability

Every owner requirement maps to a component and a phase. Requirement IDs match `requirements[]` in `AGENT_HANDOFF.json`.

| ID | Requirement | Where it lives | Phase |
|---|---|---|---|
| R-01 | Natural-language interaction (validate suitable platform) | `chat/` (Telegram primary, web UI optional) | 3 |
| R-02 | All trading analysis: indicators, TA, news | `analysis/`, `research/` | 4 |
| R-03 | Learn, validate, rank strategies for reliability and efficiency | `lab/` | 5 |
| R-04 | Detect profitable patterns, validate, promote to strategies | `lab/pattern_miner.py` | 5 |
| R-05 | Use HistData historical data for validation | `data/importers/histdata.py` | 1 |
| R-06 | Identify least-risky trades and execute them | `risk/trade_quality.py`, `risk/guardian.py` | 2 |
| R-07 | Know when markets are open/closed | `data/market_hours.py` | 1 |
| R-08 | Understand P&L, risk, lot sizing vs account size | `risk/sizing.py`, `execution/pnl.py` | 2 |
| R-09 | Timeframes 1M, 3M, 5M, extensible | `data/resample.py`, `config/settings.yaml` | 1 |
| R-10 | Capital.com integration, other brokers later | `execution/brokers/capital_com.py` | 2 |
| R-11 | Paper/demo trading first | `execution/brokers/paper.py`, demo mode | 2 |
| R-12 | Handle requests: research, graphs, stop trading, kill switch | `chat/router.py`, `control/` | 3 |
| R-13 | Visual graphs for validation, analysis, sharing | `viz/` | 3 |
| R-14 | Automatic backup to GitHub and HuggingFace | `backup/` | 6 |
| R-15 | API integrations (Capital.com, Telegram, others) | `integrations/` | 1-3 |
| R-16 | Integrate any resource to request, validate, identify data | `research/sources/`, `tools/registry` | 4 |
| R-17 | Self-research via APIs or web portals | `research/` | 4 |
| R-18 | Telegram alerts; daily/weekly/monthly reports by Telegram or email | `notify/`, `reports/` | 3 |
| R-19 | Claude and Codex JSON loop | `agent_state/` | 0 |
| R-20 | Record everything done to avoid loops | `agent_state/` decisions, validations, log | 0 |
| R-21 | Fully autonomous agentic system | `agents/`, `lab/` | 4-5 |
| R-22 | Expandable; detects overload and adds workers | `scaling/`, `workers/` | 7 |
| R-23 | Portable Docker container on RunPod | `docker/`, `infra/runpod/` | 0 |
| R-24 | NAS100 and Gold now, more later | `config/instruments.yaml` | 1 |
| R-25 | Think, search, plan, use tools/APIs/systems | `agents/`, `tools/` | 4 |
| R-26 | Owner adjusts limits and settings by natural language, safely | `control/settings_service.py` | 3 |

---

## 2. Verified External Facts and What They Force in the Design

Checked on 2026-10-02 against public documentation. **Codex must re-verify each item against live docs before relying on it** and record the result in the `validations` list of the handoff file.

### Capital.com API
| Fact | Design consequence |
|---|---|
| Separate demo host `demo-api-capital.backend-capital.com`; live host `api-capital.backend-capital.com`; streaming `wss://api-streaming-capital.backend-capital.com/connect` | Broker adapter takes `BROKER_ENV=demo|live`; demo is the default |
| Auth: API key plus a session (`POST /session`) giving `CST` and `X-SECURITY-TOKEN` headers | Session manager with auto-renew; tokens never logged |
| 2FA must be enabled on the account before an API key can be generated | Owner setup step |
| Max 10 requests/second; `POST /session` limited to 1/second per key | Central client-side rate limiter (token bucket) |
| Demo account: `POST /positions` and `POST /workingOrders` limited to 1000/hour | Order-rate budget in the Risk Guardian; relevant for 1M scalping |
| WebSocket session lasts 10 minutes unless pinged; max 40 instruments | Streaming client with ping loop and auto-reconnect |
| Price history resolutions: `MINUTE, MINUTE_5, MINUTE_15, MINUTE_30, HOUR, HOUR_4, DAY, WEEK` | **There is no 3-minute resolution. 3M candles are built by resampling 1M candles.** |
| No order-event WebSocket channel; execution events are polled from REST (`/positions`, `/workingorders`, `/history/activity`) | Reconciler polls; designed for it |
| Instruments identified by "epic" | `instruments.yaml` maps internal symbol to epic; Codex discovers epics through the market search endpoint |

### HistData.com
| Fact | Design consequence |
|---|---|
| Only tick and **M1** bars; other timeframes are derived | Resample to 3M and 5M ourselves |
| Bar prices are based on the **bid** | Ask side and spread must be modeled; cannot assume mid price |
| Timestamps are **Eastern Standard Time without daylight saving (fixed UTC-5)** | Convert to UTC on import; test around US DST changes |
| Data is organized by pair/year/month in ZIP files; Nasdaq-100 appears as `NSX/USD`, gold as `XAU/USD` in the HistData tooling | Importer maps these to `NAS100` and `XAUUSD`. **Codex must confirm current coverage dates for NSXUSD before relying on it** |
| It is a different price feed from Capital.com | Quantify the basis (price difference) on overlapping periods; treat HistData results as *hypothesis generation* and Capital.com demo forward-testing as *confirmation* |

### Consequences that shape the whole system
1. **Own data lake.** From day one the system records its own Capital.com 1M candles (bid and ask) and spread to Parquet. Over time this becomes the most accurate dataset for the actual broker.
2. **Cost realism decides everything on 1M to 5M.** A spread of a few points on a 1M index move can erase the edge. Every backtest uses a spread/slippage model calibrated from recorded Capital.com data, with a stress test at +50% costs.
3. **The Lab may widen timeframes.** The Lab starts at 1M/3M/5M as requested, but the config allows it to test 15M and 1H too, because the honest answer may be that low timeframes do not qualify.

---

## 3. System Overview

```
        Owner ── Telegram / Web chat (natural language) ── reports, charts, alerts
                          │
┌─────────────────────────▼──────────────────────────────────────────────────┐
│ CONTROL PLANE: Chat Router (deterministic fast path + LLM) · Scheduler ·   │
│ Orchestrator · Autoscaler · API/Health · Notifier · Backup                  │
└───────┬─────────────────────┬──────────────────────────────┬───────────────┘
 DATA PLANE            INTELLIGENCE PLANE              EXECUTION PLANE
 Capital.com feed      Researcher                      Risk Guardian (plain code)
 HistData importer     Analyst                         Order Manager
 Own data lake         Strategist                      Capital.com adapter
 Market hours          Critic                          Paper broker
 Resampler 1M/3M/5M    Strategy Lab (section 6)        Reconciler / P&L
        └──────────────┴──────────────┬────────────────────┘
                 STATE: Postgres · Redis · pgvector · Parquet · /workspace
                 OBSERVABILITY: structured logs · metrics · audit log
```

### Separation of authority

| Component | Thinks freely | Calls external tools | Can place orders |
|---|---|---|---|
| Researcher, Analyst, Strategist, Critic, Idea Generator (LLM) | Yes | Read-only tools | **No** |
| Chat Router LLM part | Yes | Read-only tools, chart/report tools | **No** (control commands go through the deterministic path) |
| Risk Guardian, Promotion Gate, Validator (plain code) | No | No | Approve/reject, set stage only |
| Execution Service (plain code) | No | Broker API | **Yes**, only Guardian-approved intents |
| Autoscaler (plain code) | No | RunPod API | No |

Broker credentials exist **only** in the Execution Service's environment. Tests fail the build if any LLM-side module imports broker code.

---

## 4. Data Plane

### 4.1 Sources and storage
- **Live:** Capital.com REST `prices` and WebSocket OHLC (bid/ask) for configured epics.
- **Historical:** HistData M1 importer (section 2 caveats) plus the system's own recorded data.
- **Storage:** Parquet partitioned by `symbol/timeframe/year`, queried with DuckDB. Metadata (lineage, QA results, basis measurements) in Postgres.
- **Lineage tag on every row:** `source = histdata | capital_rest | capital_ws`, so backtests can filter by source.

### 4.2 Pipeline (HistData)
`download → unzip → parse → EST(UTC-5, no DST) to UTC → QA (duplicates, gaps, outliers, zero-volume runs) → bid-only bars flagged → write Parquet → resample 3M/5M/others → record QA report`

### 4.3 Resampling
Single module `data/resample.py` builds any timeframe from 1M. Rules: bars aligned to UTC boundaries, open = first, high = max, low = min, close = last, bars with missing minutes flagged. Used for both historical and live data so backtests and live trading see identical candles.

### 4.4 Market hours (R-07)
`data/market_hours.py` answers "is NAS100 / Gold tradeable now, when does it open/close next, is a holiday or early close coming?" using, in order of authority:
1. The broker's market status from the market-details endpoint (ground truth; Codex to confirm the exact field and values).
2. `config/instruments.yaml` session rules plus an exchange holiday calendar (fallback and for forecasting).
3. Data-driven check: no new ticks for N seconds means treat as closed.

Orders are rejected by the Risk Guardian when the market is closed, within the daily break, or inside configured pre/post-event buffers. Weekend and holiday gaps are excluded from indicator warm-up logic.

### 4.5 Instruments (R-24)
`config/instruments.yaml` holds, per instrument: internal symbol, Capital.com epic, HistData code, tick size, point value, min/max/step deal size, margin factor, session rules, typical spread, news-sensitivity tags. Values for size/margin are read from the broker's market details at startup and cached, not hard-coded.

---

## 5. Intelligence and Execution Planes

### 5.1 Analysis engine (R-02)
- **Indicators:** trend (EMA/SMA, MACD, ADX), momentum (RSI, stochastic), volatility (ATR, Bollinger, volatility percentile), volume/VWAP where available, session ranges and opening ranges.
- **Technical analysis:** support/resistance and swing levels, candlestick patterns, trend structure, regime classification (trending, ranging, high-volatility, event-risk), multi-timeframe alignment across 1M/3M/5M.
- **News and macro:** economic calendar (FOMC, CPI, NFP, PMI), headlines (RSS/news APIs), macro series (rates, dollar, real yields for gold), positioning (COT, VIX), mega-cap earnings for Nasdaq. Each fact stored with `source_url`, `fetched_at`, `reliability`.
- Output is a typed `MarketSnapshot` and `AnalysisReport`, never free text.

### 5.2 Runtime agents
| Agent | Job | Tools (read-only) |
|---|---|---|
| Orchestrator | Runs the decision graph with step/token/time budgets | n/a |
| Researcher | Finds and cross-checks facts via APIs and web pages | `web_search`, `fetch_url`, `news_feed`, `calendar`, `macro_series` |
| Analyst | Interprets snapshot and research | `get_snapshot`, `get_candles`, `run_indicator` |
| Strategist | Proposes a `TradeIntent` or `NO_TRADE` (no position size field) | `get_snapshot`, `query_memory`, `quick_backtest` |
| Critic | Adversarial review; can veto | `query_memory`, `calendar` |
| Reporter | Daily/weekly/monthly reports, post-trade lessons | `query_trades`, `make_chart` |

**Analyst roster (v1).** AI analysts use the LLM; code analysts are deterministic. All of them only produce typed reports and none can place orders or change settings.

| Analyst | Type | Job |
|---|---|---|
| Technical Analyst | AI | Reads the computed indicators and levels across 1M/3M/5M; judges trend, structure, regime and timeframe agreement |
| News and Macro Analyst | AI | Calendar, headlines, macro data and sentiment/positioning inputs; cross-checks claims; flags event risk; loads a short playbook per instrument (NAS100: rates, mega-cap earnings, risk appetite; Gold: real yields, dollar, geopolitics) |
| Critic | AI | Argues against each trade idea; can veto |
| Performance Analyst | AI | Reviews closed trades and strategy decay; writes lessons |
| Idea Generator, Post-mortem Analyst | AI (Strategy Lab) | Propose strategy hypotheses; explain retirements |
| Indicator and pattern engine | Code | Indicators, support/resistance, candle patterns, volatility |
| Execution Quality Analyst | Code | Spread, slippage and liquidity by session; calibrates the cost model |
| Data Quality Analyst | Code | Gaps, stale feeds, bad prices; raises alerts |
| Risk Guardian, Pattern Miner | Code | Risk scoring, sizing, approval; statistical pattern scans |

Instrument-specific knowledge lives in config playbooks, so adding an instrument does not add an analyst. Sentiment and positioning (COT, VIX) start inside the News and Macro Analyst and can be split out later if they earn their LLM cost.

Decision cycle: trigger (bar close on 1M/3M/5M, session open, news event, price alert) → snapshot → research loop → analysis → strategy proposal → critique (max 2 revisions) → Risk Guardian → Execution → monitor → journal.

Autonomy features: plan/act/observe/reflect loops with hard budgets; tools registered in `config/tools.yaml` (built-in tools and optional MCP servers, each with a permission level); a claims-verification step that rejects unsourced facts; default to `NO_TRADE` when confidence is low or data conflicts.

**Untrusted content rule:** everything fetched from the web or news is wrapped and labeled as data. No fetched text can trigger a tool call that changes state.

### 5.3 Risk, sizing, P&L (R-06, R-08)

**Risk Guardian** (plain code, no LLM, fully unit-tested). It rejects an intent if any rule fails:
- no stop-loss; stop too tight or too wide relative to ATR; reward:risk below minimum
- risk per trade above limit; daily/weekly loss limit hit; drawdown limit hit (halts system until manual reset)
- too many open positions overall, per instrument, or combined NAS100+Gold exposure above cap
- market closed, break, news blackout, or stale data
- spread above ceiling or spread/ATR ratio too high
- order-rate budget exceeded (Capital.com demo limit of 1000 position requests/hour)
- strategy's stage cap exceeded
- kill switch or pause active

**Position sizing** (`risk/sizing.py`, never LLM-supplied):
```
risk_amount   = account_equity × risk_per_trade_pct
stop_distance = |entry − stop| in price points
size          = risk_amount / (stop_distance × value_per_point_per_unit)
```
then rounded **down** to the broker's size step, clamped to min/max deal size, and checked against available margin and a leverage cap. If the minimum deal size already exceeds the risk budget, the trade is rejected. "Account market cap" in the original request is interpreted as **account equity** (balance plus open P&L).

**Trade quality score** (the "least risky trades" requirement): each intent gets a deterministic score from stop distance in ATR, spread/ATR, regime fit, multi-timeframe agreement, event proximity, liquidity/session, and the strategy's live reliability. Only intents above a configurable threshold are eligible. Honest note: this selects *lower-risk* setups; no trade is risk-free.

**P&L engine** (`execution/pnl.py`): realized and unrealized P&L, fees/financing, R-multiples, equity curve, drawdown, exposure and margin use, all reconciled against the broker's account data.

### 5.4 Execution
- `BrokerAdapter` interface: `get_account`, `get_positions`, `place_order`, `modify_order`, `close_position`, `cancel_order`, `get_open_orders`, `get_market_details`.
- Adapters: `PaperBroker` (simulated fills with modeled spread and slippage), `CapitalComBroker` (demo and live hosts).
- Every order carries a server-side stop-loss and an idempotency key. The Reconciler polls broker truth, alerts on mismatch, pauses entries, and heals state.
- Singletons (Risk Guardian, Execution Service, decision loop) are protected by a Redis leader lock so no scaling event can duplicate orders.

### 5.5 Memory
Postgres for cycles, decisions, intents, orders, fills, positions, snapshots, research items, lessons, audit log. pgvector for lesson/research similarity. Append-only audit log stores every prompt, tool call, intent, risk decision and order under a `cycle_id` so any trade can be replayed.

---

## 6. Strategy Lab (R-03, R-04, R-05)

### 6.1 Purpose
Continuously find, test, rank, deploy and retire strategies **without human approval**. Promotion decisions come from plain-code gates the AI cannot edit.

### 6.2 Components
| Component | Type | Role |
|---|---|---|
| Idea Generator | LLM | Hypotheses from research, observations, lessons; outputs a `StrategySpec` |
| Pattern Miner | Plain code (+LLM naming) | Searches data for recurring, statistically meaningful patterns (opening range behaviour, session effects, volatility compression/expansion, mean-reversion after extension, event reactions) and emits candidate specs |
| Experiment Runner | Worker task | Backtests, parameter sweeps, walk-forward |
| Validator | Plain code | Statistical gates; only reader of the sealed holdout |
| Promotion Gate | Plain code | Sets stage and capital cap |
| Allocator | Plain code | Splits risk budget, caps correlation |
| Decay Monitor | Plain code | Demotes decaying strategies |
| Post-mortem Analyst | LLM | Explains retirements, writes lessons |

**A pattern becomes a strategy only if it passes the same gates as any idea.** Strategies are **declarative specs** (conditions built from the approved indicator library, plus exit rules) run by a sandboxed engine. No LLM-written executable code ever runs near the broker.

### 6.3 Data for validation
1. **Hypothesis data:** HistData M1 (converted to UTC, bid-only flagged) plus the own data lake.
2. **Confirmation data:** forward test on Capital.com demo prices.
3. **Sealed holdout:** the most recent N months are locked; only `lab/validator.py` can read them.
4. **Cost model:** spread and slippage per instrument and session, calibrated from recorded Capital.com data.

### 6.4 Stage ladder
| Stage | Name | Money at risk | Up when | Down when |
|---|---|---|---|---|
| 0 | Idea | none | spec valid | invalid |
| 1 | Backtest | none | Gate 1 | Gate 1 fails: retired |
| 2 | Paper / demo forward test | none | Gate 2 | fails tolerance |
| 3 | Micro-live | 10-25% of target size | Gate 3 | decay trigger |
| 4 | Live | ramps 25/50/100% | n/a | decay trigger |
| 5 | Retired | none | n/a | n/a |

Agreed gates (stored in `config/strategy_lab.yaml`; the stricter minimums come from the earlier project's evidence rules; changeable only through the SettingsService):
- **Gate 1:** at least 300 simulated trades, of which at least 100 out-of-sample; at least 5 walk-forward windows; at least 3 separate validation periods; out-of-sample profit factor of at least 1.3 after costs; holdout profit factor of at least 1.2; at least 70% of walk-forward windows profitable; still profitable with costs +50%; neighboring parameters also profitable; passes multiple-testing correction; profitable in at least two regimes; top 5% of trades under 40% of profit.
- **Gate 2:** at least 4 weeks and 200 trades on demo; results inside the backtest confidence band; measured costs consistent with the model; zero risk-rule violations.
- **Gate 3:** at least 4 weeks and 100 micro-size trades; positive expectancy; slippage within tolerance; drawdown within 1.0x of expectation.
- **Demotion:** live drawdown above 1.5x backtest maximum, rolling 30-trade expectancy below zero, regime-fit failure, or execution deterioration. Drop one stage and halve size; a second demotion within 90 days retires it.

**Overfitting defenses (mandatory):** sealed holdout, experiment ledger counting every variant tried, realistic costs, walk-forward testing, forward testing before money, capital ramp, complexity limits, diversity requirement between live strategies.

### 6.5 Routing
`execution/router.py` sends stage 2 intents to demo/paper and stage 3 to 4 intents to the live adapter, and only when `LIVE_TRADING_ENABLED=true`.

---

## 5.9 Natural-Language Interface and Control (R-01, R-12, R-13, R-18)

(Numbered 5.9 to keep section 6 together; it is part of the control plane.)

### Platform choice (R-01 validation)
| Option | Verdict |
|---|---|
| **Telegram bot** | **Primary.** Works on phone, supports text, images (charts), files, inline buttons, commands. Already needed for alerts. |
| Web chat UI (e.g. Chainlit or similar) | Optional second channel for interactive charts and longer sessions |
| Slack / Discord / WhatsApp | Possible later behind the same `ChatChannel` interface |

All channels implement one `ChatChannel` interface, and only allow-listed owner accounts can issue commands.

### Request handling
1. **Deterministic fast path (no LLM):** `/pause`, `/resume`, `/kill`, `/status`, `/positions`, `/report`, and a keyword matcher for phrases like "stop trading", "kill", "halt", "close everything". These act immediately and confirm back. A natural-language stop request is **never** routed through the LLM first.
2. **LLM path:** everything else is interpreted into a typed intent: research question, chart request, strategy question, explanation of a trade, report request, parameter query. The LLM can call read-only tools and chart/report tools only.
3. **Dangerous requests** (going live, editing code or risk files directly) are refused in chat. Changing the *numbers* in risk limits and Lab gates is allowed, but only through the SettingsService described below.

### Stop levels
- `pause`: no new entries; open positions still managed to their stops/targets.
- `kill`: halt everything and close all positions.
- Resume after `kill` requires an explicit owner `/resume`; the agent never resumes itself.

### Changing limits from chat (R-26)
The owner can adjust risk limits and Lab gates in natural language, for example "raise max daily loss to 3%". The LLM only turns the sentence into a typed request. A deterministic `SettingsService` (no LLM) decides what happens:

| Rule | Behavior |
|---|---|
| Who | Only allow-listed owner chat IDs |
| Hard ceilings | `config/risk_ceilings.yaml` is baked into the image. A value above its ceiling is refused. Raising a ceiling needs a redeploy (Claude/Codex), never chat |
| Tightening (safer) | Shown back to the owner with a Confirm button, then applied immediately |
| Loosening (riskier) | Confirm button **plus** a one-time code from an authenticator app, then applied after a cooldown (default: next trading-day start) and cancellable before then |
| During a halt | No loosening while a daily-loss or drawdown halt is active |
| Record | Every change is versioned, written to the audit log and notified on Telegram |
| Agents | Runtime LLM agents cannot call, import or edit the SettingsService or the config files |

Changing the *rules themselves* (adding a new type of check, for example) is a code change handled through Claude and Codex. Changing the numbers is not.

The hard ceilings protect against a compromised Telegram account and against changes made in a bad moment after a loss.

### Visualization (R-13)
`viz/` produces on request or in reports: candlestick charts with trades and levels overlaid, equity and drawdown curves, strategy comparison and walk-forward charts, hour-of-day and day-of-week performance heatmaps, regime maps, backtest-vs-forward overlays. Output PNG for chat, interactive HTML for the web UI, embedded in PDF/HTML reports.

### Alerts and reports (R-18)
- Real-time Telegram alerts: trade opened/closed with P&L, risk rejections, stage promotions/demotions, kill/pause events, reconciliation mismatches, scaling events, feed or LLM failures.
- Daily, weekly and monthly reports (P&L, equity curve, drawdown, win rate, expectancy, per-strategy performance, strategy stage changes, risk usage, costs), delivered through Telegram and/or email (SMTP or an email API; configurable).

---

## 5.10 Backups (R-14)

| Destination | What goes there | What never goes there |
|---|---|---|
| **GitHub** (private repo) | Code, configs, strategy specs, prompts, docs, `agent_state/`, small report artifacts | Secrets, raw data files, DB dumps |
| **HuggingFace** (private dataset repo) | Parquet data lake, backtest results, experiment ledger, encrypted DB dumps, model/artifact snapshots | Secrets |

- Nightly scheduled backup job plus a backup on every strategy stage change.
- Fine-grained tokens limited to the specific repos; stored in RunPod Secrets.
- A secret scanner runs before every push and aborts on a hit.
- Restore procedure documented and **tested** (spin up a fresh pod from the image plus the backups). This is the real portability test.
- GitHub rejects files over 100 MB, so data uses HuggingFace.

---

## 5.11 Research and Integrations (R-15, R-16, R-17)

- `Source` interface with caching, rate limiting, reliability scoring and retries. Adapters: web search API, URL fetch, RSS/news, FRED, COT, economic calendar, and any new API added through config.
- `tools.yaml` registry: each tool has a permission level. MCP servers can be attached by config.
- The Researcher cross-checks important claims against at least two sources and records provenance.
- Integrations live in `integrations/` (Capital.com, Telegram, email, GitHub, HuggingFace, RunPod, search/news/macro APIs), each with a health check surfaced in `/status`.

---

## 5.12 Scaling (R-22)

- Metrics: queue depth, oldest-task age, worker CPU/RAM, LLM latency/errors, cycle duration versus bar interval.
- Policy in `config/scaling.yaml`: scale up when thresholds are breached for a set time; scale down after idle; hard caps on workers and hourly spend.
- Action: RunPod API creates/terminates worker pods from the same image with `ROLE=worker`.
- Priorities: P0 position management and risk, P1 decision cycles, P2 research, P3 Lab experiments, backtests, reports. Under overload, low priorities are shed first.
- Only workers scale. Risk Guardian, Execution Service and the decision loop are singletons.

---

## 7. Builder Collaboration Protocol: Claude and Codex (R-19, R-20)

### 7.1 The loop
```
Claude writes task specs ──► AGENT_HANDOFF.json (turn: codex)
        ▲                                   │
        │                                   ▼
Claude reviews, validates,        Codex implements, tests, logs
updates tasks/decisions  ◄──────  sets turn: claude
```

### 7.2 One shared file, strict turns
`agent_state/AGENT_HANDOFF.json` is the single source of truth. Rules (full text in `agent_state/README.md` and enforced by `validate_handoff.py` in CI):
1. **Only the agent named in `turn` may edit the file.** When done, set `turn` to the other agent and increment `revision`.
2. **Read before acting:** `decisions` (do not reopen anything marked final), `validations` (do not redo a passed check unless its `reverify_if` condition is true), `blockers`, `questions`.
3. **Every action is logged** in the append-only `log` with revision, agent, task IDs, files changed and a short summary.
4. **Anti-loop rules:** a task may be sent back at most twice (`review_cycles`); on the third time the agent raises a `question` for the human with options instead of arguing. A question is answered once and recorded as a decision.
5. **Definition of done:** tests pass, acceptance criteria met, evidence path recorded, `completion_note` written.
6. **Human override:** the owner can set `turn` to `human` at any time to pause both agents.

### 7.3 Division of labor
- **Claude:** specs and acceptance criteria, risk policy and gate numbers, agent prompts, architecture reviews, validation of Codex's work, documentation, owner-facing summaries.
- **Codex:** implementation, tests, Docker, CI, migrations, integrations; reports what was done and what is still needed.

---

## 8. RunPod Deployment

- **Single image, several roles** chosen by `ROLE`: `all` (everything in one pod, used at the start), `core`, `worker`, `backtest`.
- **Start with one pod** (`ROLE=all`): about 8 vCPU and 32 GB RAM, CPU only, plus a 100 GB network volume mounted at `/workspace` (Postgres data, Parquet lake, logs). Redis and Postgres run inside the pod under `supervisord` for paper phase.
- **Before going live** (Phase 9): split into `core` plus `worker` pods, and move Postgres and Redis to managed services (only `DATABASE_URL` and `REDIS_URL` change).
- RunPod pods are single containers, so no docker-in-docker or `docker compose` on RunPod; `docker-compose.dev.yml` is for local development only.
- Live-trading safeguards: all state on the network volume; reconcile with the broker before enabling trading on every start; broker-side stop-losses on every order; dead-man alert if heartbeats stop; check whether Capital.com or any broker restricts API access by IP (RunPod IPs are not guaranteed fixed).

### Environment variables (names only; values in RunPod Secrets)
```
ROLE, TRADING_MODE (paper|auto), LIVE_TRADING_ENABLED (default false), BROKER_ENV (demo|live)
CAPITAL_API_KEY, CAPITAL_IDENTIFIER (account email), CAPITAL_API_PASSWORD
LLM_PROVIDER, ANTHROPIC_API_KEY / OPENAI_API_KEY
DATABASE_URL, REDIS_URL
RUNPOD_API_KEY, RUNPOD_NETWORK_VOLUME_ID
TELEGRAM_BOT_TOKEN, TELEGRAM_OWNER_CHAT_IDS
SMTP_* or EMAIL_API_KEY, REPORT_EMAIL_TO, OWNER_TOTP_SECRET
GITHUB_TOKEN, GITHUB_REPO, HF_TOKEN, HF_DATASET_REPO
SEARCH_API_KEY, NEWS_API_KEY, FRED_API_KEY
```

---

## 9. Data Contracts (Pydantic, `core/models.py`)
`Candle`, `MarketSnapshot`, `ResearchItem/Brief`, `AnalysisReport`, `TradeIntent` (no size field), `CritiqueReport`, `RiskDecision` (approved size, reasons, rules checked), `OrderRequest` (idempotency key, stop, take-profit), `Position`, `PnLRecord`, `StrategySpec`, `ExperimentResult`, `PromotionDecision`, `Lesson`, `ChatIntent`, `Task`. All LLM outputs are schema-validated; invalid output retried once, then the cycle ends as `NO_TRADE`.

---

## 10. Configuration (`config/`)
`settings.yaml` (mode, timeframes, intervals) · `instruments.yaml` · `risk.yaml` (changed only through the SettingsService) · `risk_ceilings.yaml` (baked into the image) · `strategy_lab.yaml` (gates, changed only through the SettingsService) · `agents.yaml` · `tools.yaml` · `strategies.yaml` (hand-written seeds) · `scaling.yaml` · `schedule.yaml` (sessions, blackouts, report times) · `backup.yaml` · `.env.example`.

Adding an instrument or timeframe = config edit only.

---

## 11. Directory Layout

```
ai-trader/
├── AGENTS.md                       # instructions for Codex
├── CLAUDE.md                       # instructions for Claude
├── ARCHITECTURE.md                 # this document
├── README.md
├── pyproject.toml
├── Makefile                        # dev | test | lint | build | run-paper | validate-handoff
├── .env.example
├── .github/workflows/ci.yml        # lint, types, tests, handoff validation, secret scan, docker build
│
├── agent_state/                    # BUILDER COLLABORATION (section 7)
│   ├── AGENT_HANDOFF.json
│   ├── handoff.schema.json
│   ├── validate_handoff.py
│   └── README.md
│
├── docker/
│   ├── Dockerfile                  # single multi-role image
│   ├── entrypoint.sh               # ROLE = all | core | worker | backtest
│   ├── supervisord.all.conf
│   ├── supervisord.core.conf
│   ├── supervisord.worker.conf
│   └── docker-compose.dev.yml      # local dev only
│
├── config/ (settings, instruments, risk, strategy_lab, agents, tools, strategies, scaling, schedule, backup)
│
├── src/trader/
│   ├── main.py                     # dispatch by ROLE
│   ├── core/                       # config, models, events, db, locks, clock, logging, errors
│   ├── integrations/               # capital_com/, telegram/, email/, github/, huggingface/, runpod/
│   ├── data/
│   │   ├── importers/histdata.py   # download, EST->UTC, QA
│   │   ├── providers/              # capital_rest.py, capital_ws.py, base.py
│   │   ├── recorder.py             # own data lake (bid/ask 1M, spread)
│   │   ├── resample.py             # 1M -> 3M/5M/...
│   │   ├── market_hours.py
│   │   ├── instruments.py
│   │   ├── quality.py              # gap/duplicate/outlier checks, basis measurement
│   │   └── store.py                # Parquet + DuckDB
│   ├── analysis/                   # indicators.py, levels.py, patterns.py, regime.py, mtf.py, snapshot.py
│   ├── research/                   # sources/, cache.py, sanitizer.py, verifier.py
│   ├── agents/                     # llm/, graph.py, orchestrator, researcher, analyst, strategist, critic, reporter, budgets.py, prompts/, tools/
│   ├── risk/                       # guardian.py, rules.py, sizing.py, trade_quality.py, circuit_breakers.py, killswitch.py
│   ├── execution/                  # brokers/{base,paper,capital_com}.py, router.py, order_manager.py, position_monitor.py, reconciler.py, pnl.py
│   ├── lab/                        # idea_generator, pattern_miner, spec_schema, sandbox_engine, experiment_runner, ledger, validator, holdout, promotion_gate, allocator, decay_monitor, postmortem
│   ├── backtest/                   # engine.py, costs.py, walkforward.py, reports.py
│   ├── chat/                       # channel.py, telegram_channel.py, web_channel.py, router.py (fast path + LLM), intents.py
│   ├── control/                    # pause/kill/resume, owner allow-list, settings_service.py, totp.py
│   ├── viz/                        # candles.py, equity.py, heatmaps.py, strategy_compare.py
│   ├── notify/                     # alerts.py, formatters.py
│   ├── reports/                    # daily.py, weekly.py, monthly.py, templates/
│   ├── backup/                     # github_backup.py, hf_backup.py, secret_scan.py, restore.py
│   ├── memory/                     # journal, lessons, vector, audit
│   ├── scaling/                    # autoscaler, metrics, runpod_client, policy, registry
│   ├── workers/                    # worker.py, queue.py, handlers/
│   ├── api/                        # FastAPI: health, status, control
│   └── dashboard/                  # optional read-only UI
│
├── migrations/
├── tests/ (unit, integration, simulation, fixtures)
├── scripts/ (bootstrap_db, download_histdata, seal_holdout, preflight_check, runpod_deploy)
├── infra/runpod/                   # templates + click-by-click guide for a non-programmer
└── docs/ (runbook, risk-policy, adding-an-instrument, restore-from-backup, adr/)
```

---

## 12. Safety Invariants (tests must enforce each)
1. No LLM-side module imports or calls broker code.
2. Every order has a stop-loss; Guardian rejects otherwise.
3. Position size is computed by `risk/sizing.py` only.
4. `risk.yaml` and `strategy_lab.yaml` are read-only to every agent tool; they change only through the SettingsService, within `risk_ceilings.yaml`.
5. Guardian, Execution Service and decision loop are singletons behind a Redis leader lock.
6. Web/news content passes through `sanitizer.py` and is never treated as instructions.
7. Real orders need `LIVE_TRADING_ENABLED=true` **and** a strategy at stage 3 or 4 recorded by the Promotion Gate.
8. Any component failure fails safe: no new entries, alert the owner.
9. Only `lab/validator.py` reads the sealed holdout.
10. No LLM-generated code executes in core or execution processes.
11. `/pause` and `/kill` work without the LLM and take effect before the next order.
12. Secrets never appear in logs, backups or Git; a secret scan blocks pushes.
13. Spend caps (LLM, RunPod) enforced; exceeding them degrades to `NO_TRADE` and alerts.

---

## 13. Testing
Unit (every Guardian rule, sizing edge cases, resampling, EST-to-UTC around DST changes), contract tests per broker adapter, prompt-injection fixtures, simulation (replay recorded days through the full pipeline with a mock LLM and paper broker), chaos tests (kill Redis, drop feed, broker errors), Lab integrity (random-noise strategy must be rejected; Idea Generator cannot read holdout), stop-sign drills mid-cycle, backup-restore drill.

---

## 14. Roadmap

| Phase | Deliverable | Exit criteria |
|---|---|---|
| 0 | Skeleton: repo, Docker multi-role image, config, DB, CI, **handoff system** | Container boots locally and on RunPod; handoff validator in CI |
| 1 | Data: Capital.com data client, HistData importer, resampler, market hours, own recorder, instruments | NAS100 and Gold data stored in 1M/3M/5M; QA report; basis measured |
| 2 | Risk and execution: Guardian, sizing, P&L, PaperBroker, Capital.com demo adapter, reconciler, kill switch | Full Guardian test coverage; demo order round trip with broker-side stop |
| 3 | Chat, alerts, charts, reports | Telegram commands, natural-language requests, charts, daily report working; stop sign drill passed |
| 4 | Analysis and runtime agents | End-to-end paper cycles with full audit trail |
| 5 | Backtest engine and Strategy Lab (incl. pattern miner) | Lab promotes and rejects strategies end to end on paper; noise strategy rejected |
| 6 | Backups to GitHub and HuggingFace plus restore drill | Fresh pod restored from backups |
| 7 | Workers and autoscaler | Load test scales up and down; singletons unaffected |
| 8 | Autonomous demo soak | At least 4 weeks unattended, no safety incidents |
| 9 | Pod split, managed DB, micro-live then live via gates | Owner sets live flag; gates enforced; drills passed |
| 10 | Expand instruments, timeframes, brokers, optional self-hosted LLM | Added via config |

---

## 15. Owner Decisions

**Settled:** Lab gates raised to the stricter minimums (Gate 1: 300 trades, 100 out-of-sample, 5 walk-forward windows, 3 validation periods; Gate 2: 4 weeks and 200 demo trades; Gate 3: 4 weeks and 100 micro-live trades); starting risk limits (0.5% per trade, 2% daily loss, 10% drawdown, 3 live strategies) with hard ceilings (1.0%, 4%, 15%, 5); timeframes 1M, 3M (built from 1M) and 5M; Capital.com demo account, 2FA and API key ready; GitHub and HuggingFace namespace `Tarek-Assaf` (private repos `ai-trader` and `ai-trader-data`); Telegram plus email (SMTP with app password) for alerts and reports; Telegram as the natural-language interface; fresh repo with selective reuse of the earlier project, audited in `docs/legacy_audit.md`.

**Still open (also in `questions[]` of the handoff file):**
1. Hosted LLM provider and monthly token budget.
2. Confirm Capital.com is permitted in your jurisdiction and understand tax/regulatory treatment before live trading.
