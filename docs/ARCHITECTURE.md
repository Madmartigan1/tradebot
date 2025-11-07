# Coinbase Trade Bot — Architecture

> Version: v1.1.6 (Autotune EMA + Final polish)

This document maps the moving parts of the bot, how they interact, and where the key extension points live. It reflects the codebase you shared:

- `main.py`
- `bot/config.py`
- `bot/tradebot.py`
- `bot/indicators.py`
- `bot/strategy.py`
- `bot/orders.py`
- `bot/utils.py`
- `bot/persistence.py`
- `bot/constants.py`
- `bot/autotune.py`

---

## 1) High‑level overview

The bot is an EMA crossover system with advisor vetoes (RSI/MACD), optional maker-first order placement, and a “Quartermaster” module that exits positions based on take‑profit and time‑in‑trade stagnation. Market regime detection (“AutoTune”) nudges knobs in a controlled way.

**Key data flows:**

1. **Market data** → WS ticker and (optionally) WS candles. If `mode=local`, we aggregate candles from tickers.
2. **Indicators** → EMA(40/120 default) + RSI/MACD compute on candle close.
3. **Signals** → EMA cross + confirm_x candles (+ localized band grace for `local` mode) feed BUY/SELL intent.
4. **Guards** → Per‑coin cooldown, daily spend cap (BUY‑only), position checks before SELL, hard stop, advisor veto.
5. **Quartermaster** → Pre‑EMA check on each candle: decide immediate SELL for take‑profit or stagnation.
6. **Orders** → Prefer maker LMT (post‑only) with per‑coin bps offsets; market for forced exits.
7. **Reconcile** → Immediate fills after each order; periodic historical fills sweep; CSV + portfolio persisted.

---

## 2) Components

### 2.1 `TradeBot`
**Responsibilities:** runtime orchestration, WS/REST integration, signals, orders, reconciliation, P&L.

- **WS layer** (`coinbase.websocket.WSClient`)
  - Subscribes to ticker for all coins.
  - Subscribes to WS candles if `mode=ws`; otherwise builds candles locally.
  - Heartbeats (best effort).
  - Resilient loop with exponential back‑off and re‑subscription.

- **Local candle aggregation** (`CandleBuilder`)
  - Buckets tick prices into OHLCV at `granularity_sec`.
  - On bucket rollover, queues a "settled" close with a small delay (`local_close_settle_ms`, default 150 ms) to avoid boundary races; flushes before and after each WS message.

- **Indicators** (`indicators.py`)
  - `EMA`, `RSI`, `MACD` updated on **candle close**.
  - EMA periods (`short_ema`, `long_ema`) are now also AutoTune-adjustable — tuned every few hours (default 3h).

- **Signal engine**
  - Computes **relative** trend using EMA cross with deadband (`ema_deadband_bps`).
  - Requires `confirm_candles` consecutive candles; in `local` mode, one neutral band candle preserves the count (band‑grace).
  - Enforces per‑coin cooldown.
  - `confirm_candles` and `ema_deadband_bps` now have CLI flags and may be nudged by AutoTune (bounded).

- **Advisors** (`strategy.py`)
  - Optional veto layer with RSI ceiling/floor and MACD histogram thresholds (bps‑normalized).
  - `AdvisorSettings` encapsulates parameters and normalization.

- **Quartermaster exits** (pre‑EMA)
  - **Take‑profit:** exit when profit ≥ `take_profit_bps`; optionally skip if MACD momentum is strong (`quartermaster_respect_macd`, `flat_macd_abs_max`).
  - **Stagnation:** when `hold_hours` ≥ `max_hold_hours` and price has drifted less than `stagnation_close_bps` in either direction.
  - Enforces dust suppression (min base size / min market base size) and own lightweight throttle.
  - Uses **market SELL** (forced immediate) for decisive exits.

- **Orders** (`orders.py`)
  - **Maker‑first** by default with per‑coin offsets (`maker_offset_bps_per_coin`, fallback `maker_offset_bps`).
  - SELL sizes are clamped to *live* available base to avoid reserve/hold issues.
  - Forced exits (`take_profit`, `stagnation`, `stop_loss`) always use **market**.
  - Success normalization via `_resp_ok` prevents false positives on rejected post‑only orders.

- **Spend & cooldown** (`utils.py`)
  - `SpendTracker` tracks BUY notional per UTC day; `daily_spend_cap_usd` gates further BUYs.
  - `LastTradeTracker` enforces per‑coin cooldown (`per_coin_cooldown_s`).

- **Portfolio & P&L** (`persistence.py` + `utils.py`)
  - Positions, cost basis, realized P&L persisted (`PORTFOLIO_FILE`).
  - Processed fills set pruned to `processed_fills_max` to prevent memory growth.
  - `trades.csv` captures side, size, price, quote, fee, liquidity, pnl, slippage, hold_time_sec, and **entry/exit reason**.

- **Reconciliation**
  - Immediate fills fetch by `order_id` after placement.
  - Periodic sweep (`reconcile_recent_fills`) within a sliding time window (clamped to 6–168h).

- **AutoTune** (`autotune.py`)
  - Computes regime votes on a separate timeframe (`autotune_vote_interval`, default 15m) using its lookback.
  - Applies **bounded** knob nudges (confirm, cooldown, RSI/MACD, deadband, short_ema/long_ema, per‑coin offsets) depending on winner or blend share.
  - Logs regime and deltas; trading timeframe remains at the candle interval (e.g., 5m).

---

## 3) Configuration (`config.py`)

- **Trading universe**: `coin_ids`
- **Mode**: `mode = "ws" | "local"`
- **Interval**: `candle_interval = "1m"|"5m"|...` → `granularity_sec`
- **Warm‑up**: `warmup_candles`, `min_candles`, `confirm_candles`
- **EMA**: `short_ema`, `long_ema`, optional `ema_params_per_coin`
- **Advisors**: `enable_advisors`, `rsi_*`, `macd_*`, `ema_deadband_bps`
- **Quartermaster**: `enable_quartermaster`, `take_profit_bps`, `max_hold_hours`, `stagnation_close_bps`, `quartermaster_respect_macd`, `flat_macd_abs_max`, `full_exit_shave_increments`
- **Spend/cooldown**: `usd_per_order`, `daily_spend_cap_usd`, `per_coin_cooldown_s`, `hard_stop_bps`
- **Orders**: `prefer_maker`, `prefer_maker_for_sells`, `maker_offset_bps(_per_coin)`, `maker_reprice_*`
- **Data hygiene**: `use_backfill`, `processed_fills_max`, `lookback_hours`, `mid_reconcile_*`, `reconcile_on_sell_attempt`
- **WS/REST**: reconnect backoff, retry budgets, soft RPS limit.

A `validate_config(...)` routine coerces unsafe values and logs warnings.

---

## 4) Threads & state safety

- **WS thread** handles messages; **main thread** starts and supervises.
- Shared structures (portfolio, processed fills, CSV write) are protected with `RLock` in critical paths.
- `_local_settle_q` guards candle close ordering for `local` mode.

---

## 5) Persistence & files

- `trades.csv` – detailed trade KPIs incl. entry/exit reason & slippage.
- `portfolio.json` – positions, cost basis, realized P&L, entry timestamps.
- `processed_fills.json` – dedupe set with prune to max keys.
- `trade.log` – session P&L footers and runtime.

---

## 6) Sequence diagrams (ASCII)

### 6.1 Candle close (WS or local)
```
WS tickers → CandleBuilder ──┐
                             ├─(on rollover + settle)→ _on_candle_close(pid, t, px)
WS candles  ─────────────────┘
                                  │
                                  ├─ update EMA/RSI/MACD, ticks++
                                  ├─ Quartermaster pre‑check → optional MARKET SELL
                                  └─ if warmed & confirmed → evaluate_signal → place_order
```

### 6.2 Order and reconcile
```
place_order → (maker? limit : market)
     │
     ├─ on success: SpendTracker.add (BUY‑only), cooldown stamp
     ├─ fetch fills(order_id) → mutate portfolio + trades.csv
     └─ periodic reconcile_recent_fills within lookback window
```

---

## 7) Extension points

- Add new advisors to `strategy.py`; wire thresholds into `AdvisorSettings`.
- Extend Quartermaster with trailing‑stop logic.
- Add per‑coin EMA/confirm overrides via `ema_params_per_coin`.
- Enrich AutoTune with KPI‑weighted offset learning.

---

## 8) Operational invariants

- Indicators update **only** on closed candles.
- No SELL attempts without a position (cross‑checked with live available).
- Daily cap blocks **BUY** only; SELLs continue.
- Forced exits are **market**; maker remains default for entries and discretionary exits.

