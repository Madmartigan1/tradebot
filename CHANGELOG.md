# Changelog

All notable changes to this project will be documented in this file.
This format loosely follows *Keep a Changelog* and uses tags for versions.


## [1.1.1] – 2025-10-21
### Added
- **Watchdog subsystem** — monitors websocket health, sends periodic pings, re-subscribes automatically, and triggers reconnects if idle too long.  
  - Warns on inactivity (`ws_idle_warn_s`)  
  - Reconnects after timeout (`ws_idle_reconnect_s`)  
  - Optionally flips to local candle aggregation after repeated failures (`ws_idle_flip_to_local_after`)  
  - Performs periodic re-subscriptions (`ws_resubscribe_interval_s`)  
  - Integrated seamlessly with the existing `run_ws_forever()` loop.

### Removed
- **Dead code cleanup**: the unused `_maybe_reprice_resting()` method and related config keys were removed for clarity.

### Changed
- Minor logging improvements: startup now announces candle mode, interval, and warmup status for better visibility.
- Safer SELL clamp logic (reinforced from v1.1.0) retained and verified.

**Result:** Tradebot now self-heals when Coinbase WebSocket stalls, keeping the EMA captain and Quartermaster trading smoothly without manual restarts.


## [1.1.0] - 2025-10-17
### Added
- **API response normalization** via `_resp_ok()`:  
  Unified success detection for both `market` and `limit` orders, ensuring that rejected post-only LIMITs no longer count toward daily spend.
- **Expanded Quartermaster module**:
  - Dual-condition exit logic (take-profit and stagnation) fully integrated.
  - Dust and minimum-market-size suppression window (default 30 min) prevents repeated micro-sell attempts.
  - Internal cooldown and timestamp throttling for deterministic, single-action exits.
- **Live balance seeding**: if portfolio cache is empty but a live balance exists, the bot now auto-seeds the position to stay in sync after restarts.
- **Local candle settle queue**:  
  Closed candles in `local` mode are queued with a configurable settle delay (`local_close_settle_ms`, default 150 ms) to prevent boundary races.
- **Swab diagnostic hooks**:  
  Added explicit `YO SWAB!` log calls during processed-fill pruning and hygiene events.

### Changed
- **Maker and market submission paths** now call `_resp_ok()` and return `(ok, resp)` consistently.
- **SpendTracker logic**: BUY spend only advances when `_resp_ok()` returns `True`; post-only rejections no longer consume daily quota.
- **Quartermaster exits** trigger through `place_order()` so that `exit_reason` is logged correctly in `trades.csv`.
- **Candle flush discipline**: `_flush_local_settle()` runs before and after every WS ticker message for tighter timing.
- **Hold-time analytics**: `entry_time` timestamps persist cleanly through restarts and are cleared on full position exit.

### Fixed
- **False positive daily cap triggers** from previewed or rejected orders.
- **Dust edge cases** that could re-fire Quartermaster exits on sub-increment balances.
- **Floating-point shave drift** when full-exit rounding approached the increment boundary.

### Notes
- Backward-compatible with v1.0.9 portfolio and fill files.  
- `trades.csv` gains no new columns (headers unchanged from v1.0.9).  
- The `_resp_ok()` helper is now the authoritative gate for all order-success checks.



## [1.0.9] - 2025-10-12
### Added
- **Graceful shutdown on all platforms** with unified `_request_shutdown()` for `Ctrl+C`/`SIGTERM` and thread exceptions; optional `SIGBREAK` on Windows.
- **Uncaught-exception hooks** (`sys.excepthook`, `threading.excepthook`) that log, request shutdown, and exit cleanly.
- **Elapsed-time AutoTune refresh** (one-shot after N hours) that reuses the live REST client for consistent previews and lighter rate limits.
- **Boot sequence hardening** to **Reconcile → AutoTune → Websocket** so tuning sees KPI/telemetry at startup.
- **Richer telemetry** including regime votes, per-product offsets, and an advisory “would disable” list.

### Changed
- **Centralized shutdown behavior** so signals, exceptions, and loop exits follow the same cleanup path (close WS, flush, exit).
- **Windows-friendly control flow** relying on `KeyboardInterrupt` with optional `SIGBREAK` for `Ctrl+Break`.
- **AutoTune client reuse** via `CONFIG._rest` to match session auth and rate limits.
- **Mid-session reconcile cadence** uses short sleep steps to keep `Ctrl+C` responsive.
- Expanded trades.csv schema to include per-trade fees, realized P&L, quote spend/proceeds, order reason, and sizing metadata (base_increment/min_market_base_size/shave), enabling accurate cash-flow and P&L reconciliation.
- Legacy CSVs are not backward-compatible; sorting is now standardized by execution time (newest first).

### Fixed
- **Safer bot construction / open** with explicit logging and clean exit if `TradeBot` init or `bot.open()` fails.
- **Clearer order diagnostics** for post-only maker rejections (`INVALID_LIMIT_PRICE_POST_ONLY`) and preview outcomes.

### Notes
- **State-compatible** with v1.0.8 (no schema/header changes).
- If `.state/portfolio.json` was deleted, positions/cost basis will rebuild from fills and future trades.
- Elapsed AutoTune refresh defaults to **enabled (4h)**; adjust or disable in config.



## [1.0.8] - 2025-10-09
### Added
- **Persistent `entry_time` tracking** across runs for accurate hold-time analytics.
- **Full-exit “shave” logic** to prevent dust mismatches and `INSUFFICIENT_FUND` preview errors.
- **Live-balance sanity check** before any SELL to eliminate phantom exit attempts.
- **Processed-fills helper integration** for automatic pruning and safer persistence.
- **Header consistency audit** that validates `trades.csv` headers at startup.
- **New crewmember — 🧹 The Swab:**  
  Keeps the decks spotless and the logs consistent. The Swab ensures no duplicate fills, stale positions, or misaligned records remain aboard.  
  *Fun fact:* the name “Swab” was inspired by *Captain Ron* — because every good ship needs a swab.
  - Adaptive alpha curve for smoother yet more decisive BLEND transitions.
- Introduced per-knob learning rates, quantized steps (0.5 bps), and min visible delta (0.25 bps).
- Implemented per-vote safety cap (2 bps) to prevent over-correction.
- BLEND now moves current knobs toward the winning regime preset instead of averaging two presets.
- Ensures clear uptrend/downtrend/choppy “SNAP” modes still use their exact golden presets.
- Added optional [AUTOTUNE DRIFT] telemetry line showing deviation from choppy baseline.

### Changed
- Improved SELL logging with detailed `held`, `avail`, and `base_inc` diagnostics.
- Cleaned duplicate imports, clarified exception handling, and tightened synchronization around state writes.
- Refined reconcile and immediate-fill paths to prevent reprocessing old trades.

### Fixed
- Quartermaster loop prevention on previously sold assets.
- Occasional preview errors when exiting full positions.
- Minor indentation and persistence alignment issues in `tradebot.py`.
- Minor bug fix: In autotune.py accept optional rest= client in autotune_config to fix mid-session retune crash (unexpected keyword argument 'rest') and reuse the bot’s authenticated REST client.

### Notes
- Fully **state-compatible** with v1.0.7 (no schema or header changes).
- `.state/portfolio.json` and `.state/trades.csv` can be preserved seamlessly.
- Fees remain logged as `0.0` when Coinbase fee-free campaigns are active.



## [1.0.7] - 2025-10-07
### Added
- **Regime voting decoupled from trading candles:**
  - New `autotune_vote_interval` (default `15m`) allows market regime detection on higher timeframe while trading stays on `5m`.
  - `autotune_vote_min_candles` ensures enough samples for accurate voting (default `72` = ~18h on 15m candles).
- **KPI-aware telemetry:**
  - AutoTune now runs **after** portfolio reconciliation, so advisory lists reflect real 3-day trade performance instead of always showing `no_kpi`.
  - Each advisory entry now includes a short reason, e.g.  
    `inactive_3d`, `no_kpi`, or `neg_pnl_3d_bps=-12.5,trades=5`.
- **Improved offset logging:**  
  - Per-product offsets (`maker_offset_bps_per_product`) now always print clearly after KPI nudges.
- **Quartermaster module (take-profit + stagnation logic):**
  - New 6 % take-profit band triggers a market SELL once unrealized P&L ≥ 600 bps.
  - 24-hour stagnation rule (“the broom”) closes flat trades (|P&L| < 2 %) with near-zero MACD momentum.
  - Quartermaster acts before EMA crossover logic; all exits are logged with `exit_reason` (`take_profit`, `stagnation`).
- **Trade tagging & CSV logging:**
  - Each trade now records `entry_reason` / `exit_reason`, `hold_time_sec`, fees, liquidity, and per-fill P&L.
  - Dry-run mode suppresses writes for hygiene.


### Changed
- **Startup order:**  
  Startup sequence now performs reconcile → AutoTune → WebSocket subscribe for accurate telemetry and consistent initialization.
- **Voting logic:**  
  Uses dedicated vote interval/timeframe; ensures regime votes (e.g. uptrend/choppy/downtrend) are based on the proper granularity.
- **Candle fetch:**  
  Candles are now sorted by timestamp (`start`/`time`) to prevent out-of-order history from Coinbase API responses.
- **Maker vs Market policy:**  
- Normal EMA/RSI/MACD trades continue to use maker (limit) orders; Quartermaster exits always use market for decisive execution.
- Added a one-shot, elapsed-time AutoTune thread that reuses the live bot’s authenticated REST client (no extra keys/clients) and restores original lookback after running.
- Improved Ctrl+C handling — sets a global shutdown flag so background threads exit cleanly, calls bot.close(), then terminates gracefully.

### Notes
- Dry-run mode still skips real fills, so KPI data will remain empty during paper sessions (guarded to prevent noise).
- Live sessions automatically populate KPI telemetry after a few fills.
- Indicator structures (EMA/RSI/MACD) remain unchanged; all tuning logic purely affects strategy sensitivity and reporting.
- The 24 h stagnation rule applies globally across all assets.  
- `lookback_hours = 120` recommended for multi-session daily runs to rebuild hold-time accuracy.



## [1.0.6] - 2025-10-04
### Changed
- **Golden Choppy presets:** Adjusted RSI, MACD, and cooldown thresholds for more effective trading in sideways/choppy conditions:
  - `rsi_buy_max=65`, `rsi_sell_min=35`
  - `macd_buy_min=+2.0`, `macd_sell_max=-2.0`
  - `per_product_cooldown_s=600`
- **Uptrend/downtrend refinements:** 
  - Uptrend cooldown kept tighter (`420s`) to participate early.
  - Downtrend cooldown raised to `900s` for safety, reducing over-trading in weak markets.
- **Logging polish:** Daily BUY cap notification now prints with blank lines before/after (`\n`) to stand out more clearly in logs.

### Notes
- EMA parameters unchanged (40/120 baseline).  
- Indicator periods remain static; only thresholds and cooldowns adjusted.  
- Lifetime P&L continues to be read from `portfolio.json`. Ensure `.state` is copied forward between versions.



## [1.0.5] - 2025-09-29
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
