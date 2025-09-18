# Changelog

All notable changes to this project will be documented in this file.
This format loosely follows *Keep a Changelog* and uses tags for versions.

## [v1.0.1] - 2025-09-18
### Added
- **Candle-based trading mode**: 5-minute candles by default, with optional WS candles or **local aggregation** from ticker; warm-up/backfill and `confirm_candles=3`. :contentReference[oaicite:0]{index=0} :contentReference[oaicite:1]{index=1}
- **KPI CSV logging** to `.state/trades.csv` (slippage, fees, liquidity, hold time, etc.). :contentReference[oaicite:2]{index=2} :contentReference[oaicite:3]{index=3}
- **Advisors refactor**: `strategy.py` introduces normalized **MACD in bps** + one-sided **RSI veto** (BUY blocked if overbought, SELL blocked if oversold). :contentReference[oaicite:4]{index=4}

### Changed
- **Defaults tightened & risk controls**: `dry_run=False`, `usd_per_order=20`, `daily_spend_cap_usd=120`, `per_product_cooldown_s=900`, `hard_stop_bps=120`. :contentReference[oaicite:5]{index=5}
- **EMA config**: global `short_ema=40 / long_ema=120`, `min_candles=120`, backfill `warmup_candles=200`; per-product overrides disabled. :contentReference[oaicite:6]{index=6}
- **Maker logic**: optional `prefer_maker_for_sells`; limit pricing uses exchange increments and clamps SELL size to held position; consistent formatting with `decimals_from_inc`. :contentReference[oaicite:7]{index=7} :contentReference[oaicite:8]{index=8} :contentReference[oaicite:9]{index=9} :contentReference[oaicite:10]{index=10}
- **Products**: updated list (e.g., **FIL-USD**, **DOT-USD**, **ARB-USD** added). :contentReference[oaicite:11]{index=11}

### Improved
- **Fills reconciliation**: on startup and immediately after orders; positions/P&L updated with **fees** and **liquidity flags**; processed-fills pruning. :contentReference[oaicite:12]{index=12} :contentReference[oaicite:13]{index=13} :contentReference[oaicite:14]{index=14}

### Notes
- Env template unchanged; keep real secrets in `APIkeys.env` locally. :contentReference[oaicite:15]{index=15}
- v1.0.1 runs **on candle closes** by default. To emulate tick-style behavior, reduce `confirm_candles` and/or switch `mode` to `"local"` with a shorter `candle_interval`. :contentReference[oaicite:16]{index=16} :contentReference[oaicite:17]{index=17}


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


All notable changes to this project will be documented in this file.
This format loosely follows *Keep a Changelog* and uses tags for versions.

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

