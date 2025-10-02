# Changelog

All notable changes to this project will be documented in this file.
This format loosely follows *Keep a Changelog* and uses tags for versions.


## [1.0.5] - 2025-10-02
### Added
- **Hybrid regime tuning (AutoTune):**
  - If a regime winner has ≥70% vote share → **snap** to strict regime (uptrend or downtrend).
  - If winner has 55–69% share → **blend** winner with **Choppy**, only for sensitivity knobs:
    - `confirm_candles`, `per_product_cooldown_s`
    - `rsi_buy_max`, `rsi_sell_min`
    - `macd_buy_min`, `macd_sell_max`
    - `ema_deadband_bps`
  - <55% → force **Choppy** (safe fallback).
- **Blended clamps:** conservative bounds ensure interpolated values stay in safe ranges.
- **Detailed AutoTune logging:**
  - Shows regime, mode (SNAP / BLEND / CHOPPY), winner, share, alpha (blend factor), and knob changes.
  - Advisory-only list of “would disable” products (still not enforced).
  - Per-product offset map after 3-day KPI nudges.

### Changed
- **main.py** now logs AutoTune results at startup and on the elapsed 4-hour refresh thread.
- **autotune.py** updated to v1.0.5 hybrid mixer (replacing winner-take-all logic).

### Notes
- **Indicator periods remain static** (RSI/MACD structure untouched).
- **Product disabling stays telemetry-only**; nothing is actually turned off.
- **Offsets**: still nudged ±1 bps based on last 3-day stats, with tiered floors for majors.



## [v1.0.4] - 2025-09-26
### Changed
- **Dynamic confirmations:** `tradebot.py` now reads `confirm_candles` directly from live config on every signal check, so **AutoTune** updates (e.g., 3→4 in choppy) take effect **mid-run** without a restart.
- **Confirmation hygiene:** counters **reset on neutral** (deadband) bars and on **direction flips**; counter is lightly **bounded** to avoid runaway growth in long trends.
- **Advisor startup log:** displays the current `confirm_candles` value from config (removed stale `_confirm_need` cache and duplicate log).
- **AutoTune (choppy regime) now mirrors v1.0.2 profile** to restore turnover:
  - `confirm_candles=3`, `per_product_cooldown_s=900`
  - Advisors: `rsi_buy_max=60`, `rsi_sell_min=40`, `macd_buy_min=+3 bps`, `macd_sell_max=-3 bps`
  - `ema_deadband_bps=8`
- **Regime clamps**: Added per-regime soft rails so AutoTune can’t over-tighten in choppy; uptrend/downtrend keep v1.0.3 behavior with mild bounds.
- **No product disabling in choppy** to preserve turnover; retains prior behavior in trending regimes.
- **Maker offset sanity**: Enforced global floor/ceiling on `maker_offset_bps(_per_product)` to avoid extreme postings; still allows ±1 bps nudges from 3-day stats.

### Added
- **Readable AutoTune summary** shows `old→new` for each tuned knob, the portfolio regime vote, and per-product offset changes.

### Fixed
- Minor log-ordering issue where the advisors log could reference settings before they were fully initialized.
- **Signal confirmation**: confirmation counter now persists across candles in the same direction and resets on neutrality, preventing premature resets.
- **Dynamic config reads**: `confirm_candles` is re-read at signal time so mid-run AutoTune changes apply without restart.
- **Side-aware maker behavior**: SELLs honor `prefer_maker_for_sells` (with size clamped to held position); LIMIT is used when maker is preferred, MARKET only for hard-stop or explicit fallback.
- **KPI/CSV logging**: Added intent price + slippage (abs & bps), hold time on round trips, and guarded state writes to prevent race conditions during reconcile.

### Notes
- **Hard-stop** unchanged: if `hard_stop_bps` is set and price drops to the floor vs cost basis, bot exits via MARKET to cap downside.
- If you want trend “fast-pass” (e.g., confirm=2 only when momentum is very strong), keep it off for now—evaluate 1.0.4 for a few days first.



## [v1.0.3] - 2025-09-25
### Added
- autotune.py file that adjusts parameters dynamically (see /docs/README.pdf for more details)
- Fixed Control+C for graceful shutdown. Trades are reconciled before exit.
- Added a mid-session reconciliation in case there is a buy and same asset is a candidate for a sell later.
- Option to run an intraday auto tuning.

### Changed
- Risk is now adaptive based on market conditions. Lookback is 18 hours by default.
- Advisors(RSI/MACD) are part of dynamic parameter modifications.

### Improved
- Shutdown is preceded by statemachine saving data in trades.CSV



## [v1.0.2] - 2025-09-18
### Added
- Repricing for resting maker orders: `reprice_each_candle`, `reprice_if_unfilled_candles`, `max_reprices_per_signal`, `reprice_jitter_ms`, plus per-asset `ttf_target_candles_per_product`.
- CSV KPIs now include `slippage_abs`, `slippage_bps`, and `hold_time_sec`.
- `BOT_STATE_DIR` lets you relocate `.state/` safely; auto-creates the directory.
- Trade log rotation to keep `trade_log.txt` tidy.

### Changed
- Risk defaults: daily BUY cap increased to **$160**.
- Advisors: defaults relaxed to RSI buy≤60 / sell≥40; MACD thresholds ±3 bps.
- EMA deadband (`ema_deadband_bps=8.0`) to reduce crossover flapping.
- **Env var**: portfolio now read from `PORTFOLIO_ID` (not `COINBASE_PORTFOLIO_ID`).

### Improved
- Immediate fills update P&L and CSV with intent-based slippage and position hold time.
- Startup reconcile remains available with `lookback_hours`.




## [v1.0.1] - 2025-09-18
### Added
- **Candle-based trading mode** (default **5m**): supports WS candles or **local aggregation** from ticker; warm-up/backfill and `confirm_candles=3`.
- **KPI CSV logging** to `.state/trades.csv` (slippage, fees, liquidity, hold time, etc.).
- **Advisors refactor**: normalized **MACD in bps**; one-sided **RSI veto** (blocks BUYs if overbought, SELLs if oversold).

### Changed
- **Risk & defaults**: `dry_run=False`, `usd_per_order=20`, `daily_spend_cap_usd=120`, `per_product_cooldown_s=900`, `hard_stop_bps=120`.
- **EMA config**: global `short_ema=40 / long_ema=120`, `min_candles=120`, backfill `warmup_candles=200`; per-product overrides disabled.
- **Maker logic**: optional `prefer_maker_for_sells`; limit pricing uses exchange increments; SELL size clamped to held position; consistent decimal formatting.
- **Products**: list updated (e.g., **FIL-USD**, **DOT-USD**, **ARB-USD** added).

### Improved
- **Fills reconciliation** on startup and immediately after orders; positions/P&L updated with **fees** and **liquidity flags**; processed-fills pruning.

### Notes
- Runs **on candle closes** by default. For faster behavior, set `confirm_candles=1` and/or `candle_interval="1m"`.
- Env template unchanged; keep real secrets in `APIkeys.env` locally.

### Improved
- **Fills reconciliation**: on startup and immediately after orders; positions/P&L updated with
  **fees** and **liquidity flags**; processed-fills pruning.

### Notes
- Still **tick-based** (not yet candle aggregation).
- Env template unchanged; keep real secrets in `APIkeys.env` locally.



## [v1.0.0] - 2025-09-18
### Added
- First **stable release** (no longer beta).
- New **advisor module** (`strategy.py`) to hold RSI/MACD thresholds and veto logic separately.
- Full **PDF documentation** (`docs/README.pdf`) linked from the root README.

### Changed
- **Config** now loads API keys/portfolio ID from `APIkeys.env` via `dotenv`; bot exits early if creds missing.
- **Maker order logic** refactored and centralized; uses per-product increments and consistent rounding.
- **Persistence** (daily spend, cooldowns, portfolio, fills) split into dedicated `persistence.py` with JSON helpers.
- Logging upgraded with **stream handler setup** (`logging_setup.py`) and more informative session footers.
- `main.py` runs cleanly under asyncio with graceful shutdown and P&L baseline setting.

### Notes
- Env template unchanged (`APIkeys.env.example` should be updated by each user locally).
- `.state/` files still local-only and ignored by Git.
- Advisors can be tuned via `strategy.py` or config thresholds.




## [v0.2.0] - 2025-09-17
### Added
- EMA crossover **dead-band** (`ema_deadband_bps=8.0`) to reduce signal flapping. 
- Immediate-fills reconciliation after live orders (updates positions/P&L; aggregates fees & liquidity flags).
- Cap on processed fills (`processed_fills_max`) to prune the dedupe store over time.
- Rotating file logging helper in `bot/logging_setup.py`.

### Changed
- Defaults tightened: `short_ema=40`, `long_ema=120`, `min_ticks=120`; several per-product `min_ticks` increased.
- Risk controls bumped: `usd_per_order=5`, `max_usd_per_day=40`, `cooldown_sec=720`, `max_loss_bps=140`.
- Maker math now lives in `bot/orders.py` and is used by `TradeBot.place_order`.
- Exchange increments fetched at startup and used for compliant rounding/formatting.
- Quote cache (bid/ask/last) used for maker pricing.

### Improved
- More informative session footer (P&L + runtime) on shutdown.
- More robust portfolio reconciliation both on startup and immediately after orders.

### Notes
- Env template unchanged (`APIkeys.env.example`); keep real `APIkeys.env` local.
- If you want previous RSI behavior, set `rsi_buy_floor=30`, `rsi_sell_ceiling=70` in `bot/config.py`.



## [v0.1.1-betaB] - 2025-09-17
### Added
- `bot/utils.py` centralizes state paths, JSON read/write, daily spend & cooldown tracking, and trade-log writing.

### Changed
- Maker-limit calculation moved into `TradeBot` and now uses live exchange increments per product; per-product bps offsets retained.
- Product set tweaks (AVAX replaces MATIC; SUI/CRO present).
- Config aligned with bot: `rsi_buy_floor` / `rsi_sell_ceiling` thresholds; removed duplicate `max_loss_bps`.

### Improved
- Startup fills reconciliation de-dupes; immediate fills after live orders update positions/P&L and log fees/liquidity.
- Per-run P&L baseline and session footer logged on shutdown.

### Notes
- `APIkeys.env.example` unchanged; real `APIkeys.env` remains local-only.
- Some duplication remains in `orders.py` / `constants.py` / `persistence.py` (to be consolidated later).



## [v0.1.0-betaA] - 2025-09-17
### Added
- EMA crossover signal engine with per-product EMA overrides.
- RSI & MACD “advisor” veto gates (overbought/oversold + momentum).
- Maker-prefer (post-only) limit orders with per-asset basis-point offsets.
- Daily USD spend cap and per-product cooldown enforcement.
- Dry-run mode (no live orders) as the default.
- Fills reconciliation on startup to align local P&L with Coinbase.
- Session footer with P&L + runtime; state persisted under `.state/`.
- Config-driven products & risk parameters in `bot/config.py`.
- `APIkeys.env.example`, `.gitignore`, `README.md`, `USAGE.md`, `requirements.txt`.

### Known limitations (beta)
- Some duplication across `tradebot.py`, `constants.py`, `orders.py`, and `persistence.py`.
- `strategy.py` not fully wired; thresholds are defined in `tradebot.py`.
- No unit tests yet for rounding/advisors/P&L accounting.
- Signals are tick-based; a candle-based strategy is planned for a future version.


<!-- latest version -->

<!-- changelog info -->

<!-- latest version 2025-09-25T11:16:01 -->
