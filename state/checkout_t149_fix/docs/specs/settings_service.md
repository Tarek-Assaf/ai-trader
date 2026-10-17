# SettingsService Spec (task C-004)

Author: Claude. For: Codex (implements as T-034 in phase 3). Requirement R-26, decision D-011.

## Purpose
Let the owner change risk limits and Strategy Lab gates in natural language from Telegram, safely. The LLM only turns a sentence into a typed request. A deterministic service (no LLM) decides and applies.

## Where it lives
`src/trader/control/settings_service.py` and `totp.py`. Nothing under `agents/`, `research/`, `lab/`, `chat/`'s LLM path or `analysis/` may import it (invariant test). The chat router calls it only after the owner's explicit confirmation step.

## Settable keys
| Key | File | Tightening means | Loosening means |
|---|---|---|---|
| `risk_per_trade_pct` | risk.yaml | lower | higher |
| `max_daily_loss_pct` | risk.yaml | lower | higher |
| `max_weekly_loss_pct` | risk.yaml | lower | higher |
| `max_drawdown_pct` | risk.yaml | lower | higher |
| `max_concurrent_live_strategies` | risk.yaml | lower | higher |
| `max_open_positions_total`, `max_open_positions_per_instrument` | risk.yaml | lower | higher |
| `max_combined_risk_pct`, `max_margin_usage_pct`, `max_effective_leverage` | risk.yaml | lower | higher |
| `min_reward_risk`, `min_trade_quality_score` | risk.yaml | higher | lower |
| `max_spread_atr_ratio`, `max_orders_per_hour` | risk.yaml | lower | higher |
| Strategy Lab gate numbers (trade counts, windows, profit factors) | strategy_lab.yaml | higher | lower |

Not settable by chat (code change via Claude and Codex only): everything in `risk_ceilings.yaml`, `LIVE_TRADING_ENABLED`, `BROKER_ENV`, rule logic, file paths, secrets.

## Hard ceilings and floors
`config/risk_ceilings.yaml` is baked into the image. For "higher is looser" keys it holds a maximum; for "lower is looser" keys it holds a minimum (floor). A request outside the bounds is refused with the bound in the message. Owner-confirmed ceilings: risk per trade 1.0%, daily loss 4%, drawdown 15%, concurrent live strategies 5. Other bounds are proposals awaiting confirmation (question Q-010).

## Flow
1. **Parse.** Chat router (LLM) produces `SettingsChangeRequest{key, new_value, reason}` or asks a clarifying question. Ambiguous or multi-key requests are split and confirmed one at a time.
2. **Authenticate.** The chat must belong to the allow-listed owner (`TELEGRAM_OWNER_CHAT_IDS`).
3. **Classify.** Compare with the current value: tightening, loosening, or no change.
4. **Validate.** Within ceilings and floors; value type and range valid; consistent with other settings (for example weekly loss not below daily loss).
5. **Show and confirm.** The service replies with the old value, the new value, the direction, when it takes effect, and a Confirm and Cancel button. Confirmations expire after 5 minutes.
6. **Second factor for loosening.** Also require a 6-digit TOTP code (RFC 6238) from an authenticator app. The secret is `OWNER_TOTP_SECRET`, set once by the owner as a RunPod Secret. Three wrong codes lock loosening for 1 hour and alert.
7. **Apply.**
   - Tightening: effective immediately after Confirm.
   - Loosening: scheduled to take effect at the next trading-day start (config `loosening_cooldown`, default `next_trading_day_start`); the owner can cancel before then with `/cancel_change <id>`.
   - **No loosening while a halt is active** (daily-loss, weekly-loss or drawdown halt, kill, or pause).
8. **Persist.** Write a new row in `settings_versions` (key, old, new, direction, requested_at, effective_at, approved_by, method) and write the new file to `/workspace/config/`. Keep all versions; support `/revert_change <id>` which is itself a tightening-or-loosening change and obeys the same rules.
9. **Record and notify.** Append to `audit_log`; send a Telegram notification on request, confirmation, application, cancellation and refusal.

## Failure behavior
- Any error leaves the current settings unchanged and alerts the owner.
- If the config file and `settings_versions` disagree at startup, use the stricter value, alert the owner, and refuse to start trading until resolved by the owner.
- If TOTP is not configured, loosening is **refused** (tightening still works).

## Tests required
- LLM-side modules cannot import the service (invariant).
- A value above its ceiling is refused; a value below its floor is refused.
- Loosening without TOTP is refused; with a wrong code is refused; lockout after three failures.
- Loosening during a halt is refused.
- Loosening is delayed to the cooldown time and is cancellable; tightening is immediate.
- Replayed or expired confirmations are refused.
- Every outcome writes to `audit_log` and sends a notification.
- Restart with a pending loosening keeps it pending.

## Natural-language examples the router must handle
- "Lower my max daily loss to 1.5%" is tightening.
- "Raise risk per trade to 0.8" is loosening (needs TOTP and cooldown).
- "Raise risk per trade to 3%" is refused (ceiling 1.0%).
- "Change the stop-loss rule" is refused as a code change; explain how to request it through Claude and Codex.
