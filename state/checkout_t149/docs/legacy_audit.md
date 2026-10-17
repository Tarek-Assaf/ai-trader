# Audit of the Earlier Project (C-005)

**Repo:** github.com/Tarek-Assaf/AI-Agent-Trading, audited at commit `52b4dda` (2026-09-28), full history of 5 commits.
**Verdict in one line:** a clean, honest research foundation, worth selective reuse for data handling, QA, experiment logging and tests. Its trained models show no usable trading edge, and it has no broker, AI-agent, chat, backup or real risk layer, so we keep the fresh architecture.

---

## 1. What it is
- 105 files, about 7,500 lines of Python, 87 test functions in 19 files.
- A research pipeline: HistData M1 candles and XAUUSD tick data (September 2026) → cleaning and anomaly checks → feature building (71 features) → direction targets → trained classifiers (5, 15 and 30 minute horizons for NSXUSD and XAUUSD) → ablation tests → experiment registry → a small research-to-paper-trading path.
- Config already mirrors our approach: research, paper and live modes; live disabled by default; strict validation requirements.

## 2. Safety and secrets
| Check | Result |
|---|---|
| `.env` or key files ever committed (all 5 commits) | None |
| API-key, token or password patterns in current files and all history | None found |
| `.gitignore` covers `.env`, data, logs, checkpoints | Yes |
| Repo visibility | Reported as public. Nothing sensitive found, but consider making it private again after we have copied what we need |

No key rotation is needed on this evidence. Caveat: pattern scanning cannot prove the absence of every secret format.

## 3. What the models actually show
Chronological train/validation/test split (no shuffling), threshold chosen on validation then frozen for test: good practice. Results are what you would expect from short-horizon direction prediction:
- Test accuracy about 50 to 53%, ROC-AUC about 0.50 to 0.53.
- Frozen-threshold test accuracy is often **below 50%** (examples: XAUUSD 5-minute baseline 49.1%, NSXUSD 5-minute baseline 47.7%).
- Higher confidence thresholds did not improve accuracy on the test period.
- Ablations (removing time, momentum or spread features) did not rescue it.

**Conclusion:** no tradable edge, before even counting spreads. This is a useful negative result, not a failure: it matches the expectation that 1 to 30 minute direction is extremely hard to predict. It was trained on only about three weeks of data (24,449 XAUUSD and 23,581 NSXUSD M1 rows, 1 to 24 September 2026), so it also cannot rule edges out.

## 4. Adopt, adapt or skip

| Old item | Decision | Goes to | Notes |
|---|---|---|---|
| `agent/data/loader.py`, `validation.py`, `anomaly_analyzer.py`, `discovery.py` | **Adapt** | T-012 | Solid QA ideas and tests. **Missing: no timezone handling.** HistData times are US Eastern without daylight saving; must convert to UTC |
| `scripts/process_tick_data.py`, `validate_tick_data.py` | **Adapt** | T-017 | Confirms HistData tick files have **bid and ask**, so real spreads can be measured and used for the cost model |
| `scripts/classify_market_gaps.py` | **Adapt** | T-015 | Gap and session logic, useful for market-hours fallback |
| `scripts/build_features.py`, `build_targets.py`, `validate_targets.py` | **Adapt** | T-040, T-053 | Feature and target code as reference; every rolling feature needs a no-lookahead test |
| `agent/research/backtest.py` | **Adapt** | T-050 | Good structure; costs are only fixed basis points. Needs spread-from-ticks costs, walk-forward and sealed holdout |
| `agent/experiment_registry.py`, `audit/experiments.py`, `experiment_query.py` | **Adapt** | T-052 | Append-only experiment log is the right idea. Move to Postgres (append-only) and count every variant for multiple-testing correction |
| `agent/strategies/strategy_interface.py`, `validator.py` | **Adapt (concept)** | T-051, T-052 | Keep the "validated before it can trade" gating; our strategies are declarative specs |
| `agent/research/ml_signal_adapter.py` | **Adapt (concept)** | T-053 | Pattern for letting a model feed signals into the Lab. Models may re-enter only through the same gates, retrained on more data |
| `config/risk.yaml` | **Reference** | C-002 | Same starting numbers you approved (0.5%, 2%, 10%, 5 positions). Its stricter evidence rules are worth considering (section 5) |
| `agent/risk/manager.py` | **Skip** | n/a | Only checks size and confidence. No stop-loss, daily loss, drawdown, spread, market hours or sizing. Superseded by the Risk Guardian |
| `agent/paper/*` | **Skip (reference)** | T-023 | Too thin: no spread, slippage or bracket orders |
| `paper_trades/trades.jsonl` | **Skip** | n/a | Test output at a fake price of 2000 for "strategy_x", not real results. Do not import |
| `models/*.joblib` | **Skip as trading models** | n/a | No edge. Keep the metrics and ablation tables as a documented lesson |
| `requirements.txt` | **Skip** | T-001 | A full environment dump including Jupyter, PyTorch and CUDA libraries. Would bloat the image and pull toward GPU use. Build a minimal dependency list instead |
| `tests/` (87 tests) | **Adapt** | with each module | Reuse where the matching module is adopted |
| `config/agent.yaml` | **Reference** | n/a | Requires human approval for paper to live. You chose autonomous promotion through gates (with a one-time owner live switch), so ours differs on purpose |

## 5. Ideas worth carrying over
1. **Stricter evidence rules.** The old risk config asks for at least 200 paper trades, 100 out-of-sample trades, 5 walk-forward windows and 3 validation periods. Our suggested gates are looser (30 paper trades, 50 micro-live trades). Because the evidence is statistical, I suggest raising our Gate 2 minimum toward the old numbers. **Open decision for the owner.**
2. **A recorded negative result.** Add the model findings to the Lab's lessons so the agent does not spend weeks rediscovering that naive direction models fail at these horizons.
3. **Tick-based spread calibration** from HistData ticks for the cost model.

## 6. Gaps the new architecture fills
Broker integration (Capital.com), runtime AI agents, research tools, chat and Telegram, charts and reports, backups, risk guardian with stop-loss and sizing, market-hours service, timezone-correct data, autoscaling, Docker and RunPod deployment, and the Claude/Codex handoff.

## 7. Limits of this audit
- I could not run the 87 tests (package installation was not possible in my workspace). Judgement is from reading the code. Codex must run them when adapting each module and record the result in `validations`.
- I did not check each rolling feature for lookahead. That belongs in the adaptation tasks.
- Data files are not in the repo (ignored), so data quality was judged only from code and the recorded experiment statistics.

## 8. Instructions for Codex
- The old repo is **reference only**. Do not copy it in wholesale.
- Before adapting a file: re-read this audit, rewrite it to our interfaces, add tests, and keep the safety invariants in `AGENTS.md`.
- Record each adapted file and its test result in `validations`.
