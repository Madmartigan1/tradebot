# Coinbase Trade Bot — Runbook

> Version: v1.1.6

This runbook covers setup, startup, monitoring, and common issues. It assumes the codebase and config you provided.

---

## 1) Prereqs

- Python 3.10+
- Coinbase Advanced Trade API key/secret with trading permissions (optionally a portfolio_id).
- Network egress to Coinbase WS/REST.
- Writable working directory for logs and persistence files.

Install deps (example):
```
pip install -r requirements.txt
```

---

## 2) Config essentials (`bot/config.py`)

- **Mode**: `mode = "ws"` for server candles or `"local"` for local aggregation from tickers.
- **Interval**: `candle_interval = "5m"` (maps to 300s).
- **Warm‑up**: `warmup_candles` (REST backfill on startup), `min_candles` (must be ≥ long EMA + confirms), `confirm_candles`.
- **EMA**: `short_ema=40, long_ema=120` (5m baseline). AutoTune adjusts these if enabled. Optionally per‑coin overrides.
- **Advisors**: `enable_advisors=True`, set RSI/MACD thresholds.
- **Quartermaster**: `enable_quartermaster=True`, set `take_profit_bps`, `max_hold_hours`, `stagnation_close_bps`.
- **Risk & spend**: `usd_per_order`, `daily_spend_cap_usd`, `per_coin_cooldown_s`, `hard_stop_bps`.
- **Orders**: `prefer_maker=True`, `prefer_maker_for_sells=True`, `maker_offset_bps_per_coin`.
- **Autotune**: `autotune_*` knobs; trading timeframe stays 5m.
- **Validation**: ensure `validate_config(CONFIG)` is called after `CONFIG = BotConfig()` in `config.py` or at import time from `main.py`.

---

## 3) Start the bot

Set your secrets in environment or however your `main.py` expects (e.g. `.env`, OS env):
```
export COINBASE_KEY=...
export COINBASE_SECRET=...
# optional
export COINBASE_PORTFOLIO_ID=...
```
Run:
```
python main.py
```
Common flags you may want immediately:
+```
+# dry-run with a small universe
+python main.py --dry-run 1 --coins=BTC-USD,ETH-USD,SOL-USD
+
+# fix your own parameters (skip AutoTune)
+python main.py --enable-autotune=0 --confirm-candles=1 --deadband=4 --cooldown-time=200
+
+# local candle aggregation and larger maker offset
+python main.py --candle-mode=local --maker-offset=18
```

Expected early log lines:
- Advisors + Quartermaster snapshot
- "Spend cap=$X | spent today=$Y"
- Backfill report per coin
- AutoTune regime + knob deltas
- "Websocket subscriptions ready."

---

## 4) Monitoring & key log lines

- **Local candles**: `LOCAL CANDLE {PID} @ {granularity}s close={px}` — confirms local aggregation is alive.
- **Signals**:
  - `[INTENT] BUY/SELL {PID} $NN (last=..., reason=ema_cross)`
  - `Advisor veto BUY/SELL ...` if RSI/MACD block a trade.
- **Quartermaster**:
  - `Quartermaster triggered SELL attempt {PID}... reason=take_profit|stagnation`
  - Dust guard: `Quartermaster: suppressing SELL {PID} for 30 min (held<required...)`
- **Spend cap**:
  - `Spend cap=$... | spent today=$...` at startup
  - `**********Daily BUY cap reached (...). Skipping further BUYs.**********`
- **Reconcile**:
  - `Reconciled fills. Lifetime P&L: $... | This run: $...`
  - "YO SWAB! Pruning processed fills (max=..., current_entries=...)…"
- **Orders**:
  - `Live BUY/SELL ... placed. Resp: {...}`
  - Maker rejection: `INVALID_LIMIT_PRICE_POST_ONLY` — adjust offsets or allow market.

### Health checks
- No WS messages for long periods → see reconnection logs; the loop auto‑retries.
- For `local` mode, you should see LOCAL CANDLE lines on every bucket rollover (e.g., every 300s for 5m).

---

## 5) Daily spend cap semantics

- Applies to **BUY** only; SELLs always allowed.
- Tracked by `SpendTracker` per UTC day (`today_total()`).
- `usd_per_order` is clipped to remaining allowance for the day; when it reaches 0, further BUYs are skipped and a Session P&L footer is written once.

If you believe cap tripped early, verify:
- Log shows actual **successful** BUYs adding to spend (dry‑run also adds spend).
- Post‑only BUY rejections **do not** add spend (success is normalized via `_resp_ok`).

---

## 6) Reconciliation

- Immediate fetch by `order_id` after placement updates portfolio/P&L and writes `trades.csv`.
- Periodic sweep (`reconcile_recent_fills`) pulls fills within the configured lookback window (clamped 6–168h), dedupes by a fingerprint, and updates portfolio.
- On SELL, if `reconcile_on_sell_attempt=True`, a quick sweep is triggered before checking position availability.

Artifacts:
- `trades.csv` — KPIs including slippage and **entry/exit reason**.
- `portfolio.json` — positions, cost basis, realized P&L, entry_time.
- `processed_fills.json` — dedupe set, pruned to `processed_fills_max`.
- `trade.log` — session P&L footers and runtime duration.

---

## 7) Troubleshooting

### A) No trades in `local` mode
- Ensure you see `LOCAL CANDLE` logs every 5 minutes; if not, WS tickers may be idle or filtered.
- Check `min_candles` vs `long_ema + confirm_candles` — warm‑up must be satisfied.
- Advisor vetoes may block entries; look for `Advisor veto` lines.
- Confirm `ema_deadband_bps` is not too wide for the asset volatility.

### B) Post‑only rejections (`INVALID_LIMIT_PRICE_POST_ONLY`)
- Tighten maker offsets for that coin or fall back to market.
- Verify `bid/ask/last` are present for maker pricing (ticker subscription is required).
- If frequent, consider temporarily `--prefer-maker-for-sells=0` for faster exits.

### C) Daily cap hit too soon
- Only **successful** BUYs increment spend; review logs around BUY placement and immediate fills.
- Dry‑run adds spend (by design) for parity with live mode.

### D) SELL attempts with tiny balances (dust)
- Quartermaster logs suppression if `held` < `max(base_inc, min_market_base_size)`; either top‑up, consolidate, or ignore.
- Quartermaster logs suppression if `held` < `max(base_inc, min_market_base_size)`; either top-up, consolidate, or ignore.
- You can also disable Quartermaster: `--enable-quartermaster=0`.

### E) WS disconnects
- The run loop auto‑retries with exponential backoff and re‑subscribes coins/channels. Look for `Websocket loop error/exit` lines and subsequent `Websocket subscriptions ready.`

---

## 8) Safe toggles & knobs

- `prefer_maker_for_sells=True` can be set to `False` if you want faster exits.
- `full_exit_shave_increments` trims the SELL size by a few increments to avoid rounding/hold races.
- `hard_stop_bps` enables an emergency exit below cost basis.
- `local_close_settle_ms` tweaks the timing of local candle close dispatch (150ms default).

---

## 9) Operational playbook

1. **Start** in `dry_run=True` until backfill, signals, and CSV look right.
2. **Go live**: set `dry_run=False` and small `usd_per_order` + `daily_spend_cap_usd`.
3. **Watch** AUTO‑TUNE logs for regime/knobs; ensure deltas are sensible.
4. **Inspect** `trades.csv` for slippage, entry/exit reasons, and hold times.
5. **Adjust** maker offsets per coin if rejections are frequent.
6. **Review** `portfolio.json` and reconcile logs daily.

---

## 10) Recovery

- On restart, portfolio and processed fills are loaded; if positions exist but cache is empty, the bot seeds from **live available** on first read.
- Unclean exits still preserve CSV and JSON due to frequent writes.

---

## 11) Default coins traded:
| ETH-USD   | XRP-USD  | ADA-USD  |
| TRAC-USD  | ALGO-USD | XLM-USD  |
| HBAR-USD  | NEAR-USD | SOL-USD  |
| DOGE-USD  | AVAX-USD | LINK-USD |
| SUI-USD   | LTC-USD  | CRO-USD  |
| DOT-USD   | ARB-USD  | IP-USD   |
| FLOKI-USD | PEPE-USD | BONK-USD |
| SEI-USD   | SHIB-USD | POL-USD  |


## 12) Glossary

- **bps**: basis points (1% = 100 bps).
- **Band‑grace**: in `local` mode, the first neutral (deadband) candle between directional moves doesn’t reset confirmation count.
- **Dust**: balance below base increment or min market base size, not tradable on exchange.

