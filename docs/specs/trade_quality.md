# Trade quality score (T-022)

The quality score is the weighted sum of seven deterministic component scores. Each component is a decimal in the closed interval [0, 1]; the weights and eligibility threshold come from the validated `config/risk.yaml`. The configured weights must include every component exactly once and sum to 1. A score equal to the threshold is eligible.

The aggregate is implemented in `trader.risk.quality.score_trade_quality`. It accepts normalized scores from trusted deterministic feature producers; it does not parse raw market text, invoke an LLM, or infer missing values. Invalid or incomplete component scores, weights, or thresholds raise `ValueError`. Callers must fail closed if a complete score cannot be produced. The Risk Guardian's hard rules (including stop-distance, spread, news blackout, and trade-quality threshold) remain independent gates.

| Component | Meaning of a higher score |
| --- | --- |
| `stop_distance_atr` | Stop distance is more suitable relative to current ATR and the configured stop policy. The Guardian still enforces its hard ATR bounds. |
| `spread_to_atr` | Spread consumes a smaller share of ATR and is comfortably below the configured spread ceiling. |
| `regime_fit` | The deterministic market-regime classification better fits the strategy's declared regime. |
| `timeframe_agreement` | A larger fraction of configured timeframes agree with the proposed direction. |
| `event_proximity` | The setup is farther from a high-impact event. The Guardian independently rejects trades inside the configured blackout window. |
| `liquidity_session` | The deterministic session/liquidity assessment indicates more favorable liquidity in an eligible session. |
| `strategy_reliability` | The strategy's trusted, persisted performance evidence is stronger over its defined evaluation window. This is not an LLM judgment. |

Component producers must document and test their normalization against their source measurements before they are wired into the decision loop. T-022 only defines and tests this stable [0, 1] aggregation contract; feature-specific normalizers and Guardian input wiring are not inferred here. Q-025 tracks the separate decision about injecting trusted sizing, quality, and stage-cap values into RiskGuardian.
