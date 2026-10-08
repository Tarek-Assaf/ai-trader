# T-112: Strategy Lab run job (`/lab`)

Author: Claude. For: Codex. Read `config/strategy_lab.yaml`, `src/trader/lab/*`, `src/trader/backtest/runner.py`, `scripts/import_histdata.py`, `docs/specs/trade_quality.md`.

Goal: one runnable job that takes HistData (NSXUSD, XAUUSD) through idea generation, tuning, walk-forward, Gate 1 and the sealed holdout, and reports a ranked pass/fail table to the owner. Pure code. No orders. The LLM is optional and OFF by default.

## Parts
1. **Data check** `scripts/lab_data_check.py`: lists lake coverage per instrument (first/last bar, months, gaps, QA flags) and exits non-zero when fewer than 24 months of M1 exist. Output is JSON plus a short text table.
2. **Run job** `scripts/lab_run.py` and `trader.lab.runner`:
   - Inputs: instruments (default both), timeframes 1m/3m/5m, idea sources (see Idea sources), `--max-ideas`, `--workers N` (default: CPU count minus 1, capped at 8).
   - Parallel by `multiprocessing` across (idea, instrument, timeframe). Deterministic seeds. A crashed worker marks its idea `error` and never aborts the run.
   - Costs: spread/slippage from the calibrated model if present, otherwise the pessimistic default in `config/strategy_lab.yaml` (add `cost_defaults` with a clear comment that T-017 replaces it). Gate 1 cost stress +50% always applies.
   - Pipeline: tune on in-sample windows, evaluate walk-forward out-of-sample, apply Gate 1 via `ValidationPipeline` including the multiple-testing correction over ALL ideas tried in the run, then read the sealed holdout once per surviving idea through the existing holdout reader.
   - Every attempt (pass, fail, error) is written to the experiment ledger. A strategy hash already in the ledger is skipped unless `--force` and logged.
3. **Telegram `/lab`** (add to `TelegramChannel.COMMANDS` and the command renderer):
   - `/lab run [gold|nas100|all]` starts the job in the background and replies at once. `/lab status`, `/lab results`, `/lab chart <id>`, `/lab stop`.
   - One run at a time. Starting a second returns "a run is already in progress".
   - Results message: ranked table (id, instrument, timeframe, trades, OOS profit factor, holdout PF, max drawdown, verdict) and the failure reason for each non-pass. Bounded length. Charts for up to 3 survivors via the existing viz module.
   - A pass means "eligible for owner-approved promotion to paper". The job never changes a stage by itself.
4. **Optional LLM roles** (flag `lab.use_llm: false` default in the Lab config): idea suggestions as typed `StrategySpec` only, summary text, post-mortems. Numbers never come from the LLM. Respect the daily/monthly caps; on cap or error continue without the LLM.

## Idea sources (all produce typed `StrategySpec`, never code)
1. **PatternMiner** (exists): opening_range, session_effect, volatility_compression, volatility_expansion, mean_reversion, each over a bounded parameter grid.
2. **Seed library** `src/trader/lab/seeds.py` (new, no LLM): well-known textbook strategies as declarative specs using only declared indicators: EMA trend pullback, EMA crossover with ATR stop, RSI mean reversion, Bollinger band reversion, Donchian breakout, VWAP-style session reversion, inside-bar breakout. Each has a short source note and a small parameter range.
3. **Tuning** (new): for every spec, search its declared parameter ranges (grid for 2 or fewer parameters, seeded random search above) on in-sample windows only. Keep the top K parameter sets, then require the neighbouring-parameter check from Gate 1. Count every combination toward the multiple-testing correction.
4. **Research-assisted ideas** (optional, `lab.use_llm: true`): an LLM role reads sanitized research items (`trader.research`) and returns `StrategySpec` through `IdeaGenerator`. Invalid output is discarded and ledgered. Off by default; capped by the LLM budget.

## Safety
- No broker, no order manager, no risk or gate file writes. The job reads Parquet and writes only the ledger and result files under `/workspace`.
- Sealed holdout access only through the existing reader; test that a second read of the same strategy fails.
- Results and logs contain no secrets.

## Acceptance criteria
- Tests with synthetic data: pure-noise ideas all fail Gate 1; a planted real edge passes; a worker crash does not stop the run; a repeated strategy hash is skipped; `/lab` rejects a second concurrent run; the results message stays within Telegram limits.
- Holdout is read at most once per strategy; tested.
- `lab_data_check` reports a gap in a fixture lake.
- Run on the Pod (owner pastes output): data check, then a small `--max-ideas 20` run, then a full run.
