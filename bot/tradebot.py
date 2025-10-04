# bot/tradebot.py
import json
import logging
import time
import uuid
import threading
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Tuple

from coinbase.rest import RESTClient
from coinbase.websocket import WSClient

# --- Candle helpers ---
class CandleBuilder:
    """Simple per-product OHLCV builder for fixed-second buckets from ticker events."""
    def __init__(self, bucket_sec: int):
        self.bucket = int(bucket_sec)
        self.start = None  # epoch seconds (bucket start)
        self.o = self.h = self.l = self.c = None
        self.v = 0.0

    def _bucket_start(self, ts: float) -> int:
        return int(ts // self.bucket) * self.bucket

    def update(self, price: float, ts: float):
        bs = self._bucket_start(ts)
        p = float(price)
        if self.start is None:
            self.start = bs
            self.o = self.h = self.l = self.c = p
            self.v = 0.0
            return None
        if bs == self.start:
            # inside same candle
            self.h = p if (self.h is None or p > self.h) else self.h
            self.l = p if (self.l is None or p < self.l) else self.l
            self.c = p
            return None
        # bucket rolled -> close previous, start new
        closed = (self.start, self.o, self.h, self.l, self.c, self.v)
        self.start = bs
        self.o = self.h = self.l = self.c = p
        self.v = 0.0
        return closed  # (start, open, high, low, close, volume)

def _parse_ws_iso(ts: str | None) -> float:
    """Parse ISO8601 '...Z' string to epoch seconds; fallback to time.time()."""
    if not ts:
        return time.time()
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return time.time()

def _granularity_enum(gran_sec: int) -> str:
    mapping = {
        60: "ONE_MINUTE", 300: "FIVE_MINUTE", 900: "FIFTEEN_MINUTE", 1800: "THIRTY_MINUTE",
        3600: "ONE_HOUR", 7200: "TWO_HOUR", 14400: "FOUR_HOUR", 21600: "SIX_HOUR", 86400: "ONE_DAY",
    }
    return mapping.get(int(gran_sec), "FIVE_MINUTE")

from .utils import (
    PNL_DECIMALS,
    TRADE_LOG_FILE,
    PORTFOLIO_FILE,
    PROCESSED_FILLS_FILE,
    log_trade,
    load_json,
    save_json,
    SpendTracker,
    LastTradeTracker,
)
from .indicators import EMA, RSI, MACD
from .orders import compute_maker_limit, decimals_from_inc
from .strategy import AdvisorSettings, advisor_allows


class TradeBot:
    def __init__(self, cfg, api_key: str, api_secret: str, portfolio_id: Optional[str] = None):
        self.cfg = cfg
        self.portfolio_id = portfolio_id if portfolio_id is not None else getattr(cfg, "portfolio_id", None)

        # Thread locking to prevent races in state + CSV writes
        self._state_lock = threading.RLock()

        # REST/WS
        self.api_key = api_key
        self.api_secret = api_secret
        self.rest = RESTClient(api_key=api_key, api_secret=api_secret, rate_limit_headers=True)

        def on_msg(raw):
            try:
                msg = json.loads(raw)
            except Exception:
                logging.debug("Non-JSON WS message: %s", raw)
                return
            self.on_ws_message(msg)

        self.ws = WSClient(api_key=api_key, api_secret=api_secret, on_message=on_msg)

        # Products
        self.product_ids = list(getattr(self.cfg, "product_ids", []))
        if not self.product_ids:
            raise ValueError("No product_ids provided in config.")

        # Indicators and per-product params
        self.short: Dict[str, EMA] = {}
        self.long: Dict[str, EMA] = {}
        self.ticks: Dict[str, int] = defaultdict(int)  # counts candles in candle mode
        self.min_ticks_per_product: Dict[str, int] = {}
        self._trend = defaultdict(int)   # -1 below, 0 band, +1 above
        self._primed = set()             # first crossover primes only (no trade)
        self._pending = {}               # product_id -> {"rel": +/-1/0, "count": N}

        # Advisors
        self.enable_advisors: bool = bool(getattr(cfg, "enable_advisors", getattr(cfg, "use_advisors", False)))
        self._rsi = {p: RSI(period=int(getattr(self.cfg, "rsi_period", 14))) for p in self.product_ids}
        self._macd = {
            p: MACD(
                fast=int(getattr(self.cfg, "macd_fast", 12)),
                slow=int(getattr(self.cfg, "macd_slow", 26)),
                signal=int(getattr(self.cfg, "macd_signal", 9)),
            )
            for p in self.product_ids
        }

        # One-sided RSI/MACD veto settings
        self.advisor_settings = AdvisorSettings(
            enable_rsi=self.enable_advisors,
            rsi_period=int(getattr(self.cfg, "rsi_period", 14)),
            rsi_buy_min=0.0,  # unused by current veto
            rsi_buy_max=float(getattr(self.cfg, "rsi_buy_max", getattr(self.cfg, "rsi_sell_ceiling", 70.0))),
            rsi_sell_min=float(getattr(self.cfg, "rsi_sell_min", getattr(self.cfg, "rsi_buy_floor", 30.0))),
            rsi_sell_max=100.0,  # unused by current veto
            enable_macd=self.enable_advisors,
            macd_fast=int(getattr(self.cfg, "macd_fast", 12)),
            macd_slow=int(getattr(self.cfg, "macd_slow", 26)),
            macd_signal=int(getattr(self.cfg, "macd_signal", 9)),
            normalize_macd=True,
            macd_buy_min=float(getattr(self.cfg, "macd_buy_min", 0.0)),
            macd_sell_max=float(getattr(self.cfg, "macd_sell_max", 0.0)),
        )

        # Log current settings snapshot (confirm reads live cfg; no cached _confirm_need)
        cur_conf = int(getattr(self.cfg, "confirm_candles", getattr(self.cfg, "confirm_ticks", 2)))
        logging.info(
            "Advisors: RSI buy<=%.1f / sell>=%-.1f (period=%d) | MACD %d/%d/%d, thresholds buy>=%.2f bps sell<=%.2f bps | "
            "deadband=%.2f bps | confirm=%d",
            self.advisor_settings.rsi_buy_max, self.advisor_settings.rsi_sell_min, self.advisor_settings.rsi_period,
            self.advisor_settings.macd_fast, self.advisor_settings.macd_slow, self.advisor_settings.macd_signal,
            self.advisor_settings.macd_buy_min, self.advisor_settings.macd_sell_max,
            float(getattr(self.cfg, "ema_deadband_bps", 0.0)),
            cur_conf,
        )

        # -------- Candle config / state --------
        # Back-compat: prefer new names, fall back to old
        self.candle_mode = str(getattr(self.cfg, "mode", getattr(self.cfg, "candle_mode", "ws"))).lower()
        # map candle_interval -> seconds if provided, else read granularity_sec
        ci = str(getattr(self.cfg, "candle_interval", "")).lower().strip()
        _ci2sec = {"1m":60,"5m":300,"15m":900,"30m":1800,"1h":3600,"2h":7200,"4h":14400,"6h":21600,"1d":86400}
        self.granularity_sec = int(_ci2sec.get(ci, int(getattr(self.cfg, "granularity_sec", 300))))

        self._last_candle_start: Dict[str, int | None] = {p: None for p in self.product_ids}
        self._cur_candle_close: Dict[str, float | None] = {p: None for p in self.product_ids}
        self._builders: Dict[str, CandleBuilder] = {p: CandleBuilder(self.granularity_sec) for p in self.product_ids}

        # EMA objects and per-product minimum candles
        for p in self.product_ids:
            se, le, mt = self._get_ema_params(p)
            self.short[p] = EMA(se)
            self.long[p] = EMA(le)
            self.min_ticks_per_product[p] = mt

        # Spend/cooldown
        self.spend = SpendTracker()
        self.last = LastTradeTracker()

        # Portfolio from fills
        self.positions: Dict[str, float] = defaultdict(float)
        self.cost_basis: Dict[str, float] = defaultdict(float)
        self.realized_pnl = 0.0

        self.run_pnl_baseline = 0.0
        self.session_cash_pnl = 0.0

        self.daily_cap_reached_logged = False
        self.stop_requested = False
        self.session_footer_written = False
        self.session_start = datetime.now(timezone.utc)
        self._last_msg_ts = time.time()
        self._reconnect_tries = 0
        self._intent = {}  # order_id -> intent metadata (price at signal etc.)
        self._entry_time = defaultdict(lambda: None)  # per-product entry timestamp when pos goes 0 -> >0

        # Increments (price/base)
        self.price_inc: Dict[str, float] = {}
        self.base_inc: Dict[str, float] = {}
        self._prime_increments()

        # Quote cache (for maker pricing)
        self.quotes = defaultdict(lambda: {"bid": None, "ask": None, "last": None})

        # Load persisted portfolio + processed fills
        self._load_portfolio()
        self._processed_fills = load_json(PROCESSED_FILLS_FILE, {})

        # Optional: backfill indicators from REST candles to warm up
        if bool(getattr(self.cfg, "use_backfill", True)):
            try:
                self._backfill_seed_indicators()
            except Exception as e:
                logging.debug("Backfill seeding failed: %s", e)

    # -------------------- helpers & config --------------------
    def _get_ema_params(self, pid: str) -> Tuple[int, int, int]:
        ovr = getattr(self.cfg, "ema_params_per_product", {}).get(pid, {})
        se = int(ovr.get("short_ema", getattr(self.cfg, "short_ema", 20)))
        le = int(ovr.get("long_ema", getattr(self.cfg, "long_ema", 50)))
        mt = int(ovr.get("min_candles", getattr(self.cfg, "min_candles", getattr(self.cfg, "min_ticks", 60))))
        return se, le, mt

    def _prime_increments(self):
        for pid in self.product_ids:
            try:
                prod = self.rest.get_product(product_id=pid)
                body = getattr(prod, "to_dict", lambda: {})()
                price_inc = body.get("price_increment") or body.get("quote_increment") or "0.01"
                base_inc = body.get("base_increment") or "0.00000001"
                self.price_inc[pid] = float(price_inc)
                self.base_inc[pid] = float(base_inc)
            except Exception:
                self.price_inc[pid] = 0.01
                self.base_inc[pid] = 1e-8

    # -------------------- persistence --------------------
    def _load_portfolio(self):
        data = load_json(PORTFOLIO_FILE, {"positions": {}, "cost_basis": {}, "realized_pnl": 0.0})
        for k, v in data.get("positions", {}).items():
            self.positions[k] = float(v)
        for k, v in data.get("cost_basis", {}).items():
            self.cost_basis[k] = float(v)
        self.realized_pnl = float(data.get("realized_pnl", 0.0))

    def _save_portfolio(self):
        save_json(
            PORTFOLIO_FILE,
            {"positions": self.positions, "cost_basis": self.cost_basis, "realized_pnl": float(self.realized_pnl)},
        )

    # -------------------- lifecycle --------------------
    def set_run_baseline(self):
        self.run_pnl_baseline = self.realized_pnl
        base_str = f"{self.run_pnl_baseline:.{PNL_DECIMALS}f}"
        logging.info("P&L baseline set for this run: $%s", base_str)

    def _backfill_seed_indicators(self):
        lookback = int(getattr(self.cfg, "warmup_candles", 200))
        enum = _granularity_enum(self.granularity_sec)
        end_ts = int(time.time())
        start_ts = end_ts - (lookback + 5) * self.granularity_sec
        for pid in self.product_ids:
            try:
                resp = self.rest.get_candles(product_id=pid, start=str(start_ts), end=str(end_ts), granularity=enum)
                body = getattr(resp, "to_dict", lambda: resp)()
                arr = list(body.get("candles", []))
                arr.sort(key=lambda c: int(c.get("start", 0)))
                for c in arr:
                    close = float(c.get("close"))
                    self.short[pid].update(close)
                    self.long[pid].update(close)
                    if self.enable_advisors:
                        self._macd[pid].update(close)
                        self._rsi[pid].update(close)
                    self.ticks[pid] += 1
                if arr:
                    last = arr[-1]
                    self._last_candle_start[pid] = int(last.get("start"))
                    self._cur_candle_close[pid] = float(last.get("close"))
                logging.info("Backfilled %s with %d candles (%s)", pid, len(arr), enum)
            except Exception as e:
                logging.debug("Backfill failed for %s: %s", pid, e)

    def open(self):
        self.ws.open()
        # Always keep ticker for quotes/maker logic
        self.ws.ticker(product_ids=self.product_ids)
        # Candles if requested via WS; otherwise we will locally aggregate from ticker
        if self.candle_mode == "ws":
            try:
                enum = _granularity_enum(self.granularity_sec)
                try:
                    self.ws.candles(product_ids=self.product_ids, granularity=enum)
                except TypeError:
                    self.ws.candles(product_ids=self.product_ids)
                logging.info("Subscribed to WS candles (%ds) and ticker for %s", self.granularity_sec, ", ".join(self.product_ids))
            except Exception:
                logging.info("WS candles unavailable; falling back to local aggregation.")
                self.candle_mode = "local"
        try:
            self.ws.heartbeats()
        except Exception:
            logging.debug("Heartbeats channel not available; continuing without it.")
        logging.info("Websocket subscriptions ready.")

    def run_ws_forever(self):
        """
        Block here running the SDK's internal run loop.
        Tries run_forever_with_exception_check with flexible signature; falls back to run_forever().
        """
        fn = getattr(self.ws, "run_forever_with_exception_check", None)
        if not callable(fn):
            logging.info("WSClient.run_forever_with_exception_check not found; using run_forever().")
            self.ws.run_forever()
            return

        import inspect
        retries = int(getattr(self.cfg, "runloop_max_retries", 5))
        try:
            sig = inspect.signature(fn)
            params = list(sig.parameters.keys())

            if not params:
                # No arguments supported
                fn()
            elif "max_retries" in params:
                fn(max_retries=retries)
            elif "retries" in params:
                fn(retries=retries)
            elif "attempts" in params:
                fn(attempts=retries)
            else:
                # Try single positional int; if that fails, call with no args
                try:
                    fn(retries)
                except TypeError:
                    fn()
        except Exception as e:
            logging.info("run_forever_with_exception_check signature mismatch (%s); using run_forever().", e)
            self.ws.run_forever()

    def close(self):
        if not self.session_footer_written:
            self.log_session_pnl()
            self.session_footer_written = True
        try:
            self.ws.close()
        except Exception:
            pass

    # -------------------- kpi csv --------------------
    def _append_trade_csv(self, *, ts_iso: str, order_id: str | None, side: str, product_id: str,
                          size: float, price: float, fee: float | None,
                          liquidity: str | None, pnl: float | None,
                          position_after: float | None, cost_basis_after: float | None,
                          intent_price: float | None, hold_time_sec: float | None):
        try:
            from .constants import TRADES_CSV_FILE
        except Exception:
            return
        headers = ["ts","order_id","side","product","size","price","quote_usd","fee","liquidity",
                   "pnl","position_after","cost_basis_after","intent_price","slippage_abs","slippage_bps","hold_time_sec"]
        quote_usd = (size * price) if (size is not None and price is not None) else None
        slippage_abs = None
        slippage_bps = None
        try:
            if intent_price and price:
                if side == "BUY":
                    slippage_abs = price - intent_price
                else:
                    slippage_abs = intent_price - price
                if intent_price > 0:
                    slippage_bps = (slippage_abs / intent_price) * 10_000.0
        except Exception:
            pass
        row = [ts_iso, order_id or "", side, product_id, f"{size:.10f}" if size is not None else "",
               f"{price:.8f}" if price is not None else "", f"{quote_usd:.2f}" if quote_usd is not None else "",
               f"{fee:.6f}" if fee is not None else "", liquidity or "",
               f"{pnl:.8f}" if pnl is not None else "",
               f"{position_after:.10f}" if position_after is not None else "",
               f"{cost_basis_after:.8f}" if cost_basis_after is not None else "",
               f"{intent_price:.8f}" if intent_price is not None else "",
               f"{slippage_abs:.8f}" if slippage_abs is not None else "",
               f"{slippage_bps:.4f}" if slippage_bps is not None else "",
               f"{hold_time_sec:.2f}" if hold_time_sec is not None else ""]
        try:
            path = TRADES_CSV_FILE
            path.parent.mkdir(parents=True, exist_ok=True)
            new_file = not path.exists()
            # single-writer guard (prevents interleaved lines)
            with self._state_lock:
                with open(path, "a", newline="") as f:
                    if new_file:
                        f.write(",".join(headers) + "\n")
                    f.write(",".join(map(str, row)) + "\n")
        except Exception as e:
            logging.debug("CSV append failed: %s", e)

    # -------------------- websocket --------------------
    def on_ws_message(self, msg: dict):
        self._last_msg_ts = time.time()
        if self.stop_requested:
            return
        ch = msg.get("channel")
        if ch == "ticker":
            events = msg.get("events") or []
            ts_now = _parse_ws_iso(msg.get("timestamp"))
            for ev in events:
                for t in ev.get("tickers", []):
                    pid = t.get("product_id")
                    price = t.get("price")
                    if pid not in self.short or price is None:
                        continue
                    try:
                        p = float(price)
                    except Exception:
                        continue
                    # cache quotes if present
                    bid_raw = t.get("best_bid") or t.get("bid")
                    ask_raw = t.get("best_ask") or t.get("ask")
                    try:
                        if bid_raw is not None:
                            self.quotes[pid]["bid"] = float(bid_raw)
                        if ask_raw is not None:
                            self.quotes[pid]["ask"] = float(ask_raw)
                    except Exception:
                        pass
                    self.quotes[pid]["last"] = p
                    # local candle aggregation
                    if self.candle_mode == "local":
                        closed = self._builders[pid].update(p, ts_now)
                        if closed is not None:
                            start, o, h, l, c, v = closed
                            self._on_candle_close(pid, start, c)

        elif ch == "candles":
            events = msg.get("events") or []
            for ev in events:
                for c in ev.get("candles", []):
                    pid = c.get("product_id")
                    if pid not in self.short:
                        continue
                    try:
                        start = int(c.get("start"))
                        close = float(c.get("close"))
                    except Exception:
                        continue
                    last_start = self._last_candle_start.get(pid)
                    if last_start is None:
                        self._last_candle_start[pid] = start
                        self._cur_candle_close[pid] = close
                        continue
                    if start != last_start:
                        self._on_candle_close(pid, last_start, self._cur_candle_close.get(pid, close))
                        self._last_candle_start[pid] = start
                    self._cur_candle_close[pid] = close

    # -------------------- signal & orders --------------------
    def evaluate_signal(self, product_id: str, price: float, s: float, l: float):
        if self.stop_requested:
            return

        # EMA crossover with small dead-band to avoid flapping
        eps = float(getattr(self.cfg, "ema_deadband_bps", 0.0)) / 10_000.0
        if s > l * (1.0 + eps):
            rel = 1
        elif s < l * (1.0 - eps):
            rel = -1
        else:
            rel = 0

        # First determination: prime trend without trading
        if product_id not in self._primed:
            self._trend[product_id] = rel
            self._primed.add(product_id)
            return

        prev = self._trend[product_id]

        # Confirmation logic
        if rel == 0:
            # neutral: don't carry confirmations across flats
            self._pending[product_id] = {"rel": 0, "count": 0}
            return

        st = self._pending.get(product_id)
        if not st or st["rel"] != rel:
            # new direction or first time seeing this product this session
            st = {"rel": rel, "count": 1}
        else:
            st["count"] += 1

        # optional cap to keep count bounded in long trends
        st["count"] = min(st["count"], 32)
        self._pending[product_id] = st

        # dynamic confirms — read live cfg so mid-run AutoTune applies
        need = max(1, int(getattr(self.cfg, "confirm_candles",
                          getattr(self.cfg, "confirm_ticks", 2))))

        if st["count"] < need:
            return

        # confirmed cross; reset pending
        self._pending[product_id] = {"rel": 0, "count": 0}
        if rel == prev:
            return
        self._trend[product_id] = rel
        signal = rel  # +1 buy, -1 sell

        cooldown_s = int(getattr(self.cfg, "per_product_cooldown_s", getattr(self.cfg, "cooldown_sec", 300)))
        if not self.last.ok(product_id, cooldown_s):
            return

        # --- v1.0.3: Just-in-time reconcile before SELL position check ---
        if signal < 0 and getattr(self.cfg, "reconcile_on_sell_attempt", False):
            try:
                self.reconcile_now(hours=getattr(self.cfg, "lookback_hours", 48))
            except Exception as e:
                logging.debug("Pre-SELL reconcile failed for %s: %s", product_id, e)

        # SELL guardrails: must hold position
        if signal < 0 and self.positions[product_id] <= 0.0:
            logging.info("Skip SELL %s: no position held.", product_id)
            return

        # Optional hard stop: if enabled and below CB by X bps, force market exit now
        if signal < 0:
            hs = getattr(self.cfg, "hard_stop_bps", None)
            if hs is not None:
                cb = float(self.cost_basis.get(product_id, 0.0) or 0.0)
                if cb > 0.0:
                    floor = cb * (1.0 - float(hs) / 10_000.0)
                    if price <= floor:
                        held = max(0.0, float(self.positions.get(product_id, 0.0)))
                        if held > 0.0 and price > 0.0:
                            quote_usd = held * price
                            ok, _ = self._submit_market_order(product_id, "SELL", quote_usd)
                            if ok:
                                self.last.stamp(product_id)
                        return

        # Advisors (optional): veto only if clearly bad
        if self.enable_advisors:
            rsi_val = self._rsi[product_id].value
            macd_hist = self._macd[product_id].hist
            if not advisor_allows("BUY" if signal > 0 else "SELL", rsi_val, macd_hist, self.advisor_settings, price):
                logging.info(
                    "Advisor veto %s %s (RSI=%s, MACD_hist=%s)",
                    "BUY" if signal > 0 else "SELL",
                    product_id,
                    f"{rsi_val:.2f}" if rsi_val is not None else "n/a",
                    f"{macd_hist:.5f}" if macd_hist is not None else "n/a",
                )
                return

        # BUY-only daily cap
        spent_today = self.spend.today_total()
        daily_cap = float(getattr(self.cfg, "daily_spend_cap_usd", getattr(self.cfg, "max_usd_per_day", 10.0)))
        remaining = max(0.0, daily_cap - spent_today)
        side = "BUY" if signal > 0 else "SELL"
        if side == "BUY" and remaining <= 0:
            if not self.daily_cap_reached_logged:
                logging.info("\n***Daily BUY cap reached (%.2f). Skipping further BUYs.***\n", daily_cap)
                self.log_session_pnl()
                self.daily_cap_reached_logged = True
                self.session_footer_written = True
            return

        notional = float(getattr(self.cfg, "usd_per_order", 1.0))
        if side == "BUY":
            notional = min(notional, remaining)

        try:
            self.place_order(product_id, side=side, quote_usd=notional, last_price=price)
        except Exception as e:
            logging.exception("Order error for %s: %s", product_id, e)

    def _submit_limit_maker_order(self, product_id: str, side: str, base_size: float, limit_price: float):
        client_order_id = f"ema-{product_id}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        p_dec = decimals_from_inc(self.price_inc.get(product_id, 0.01))
        b_dec = decimals_from_inc(self.base_inc.get(product_id, 1e-8))
        limit_price_str = f"{limit_price:.{p_dec}f}"
        base_size_str = f"{base_size:.{b_dec}f}"

        params = {
            "client_order_id": client_order_id,
            "product_id": product_id,
            "limit_price": limit_price_str,
            "base_size": base_size_str,
            "post_only": True,
        }
        if self.portfolio_id:
            params["portfolio_id"] = self.portfolio_id

        try:
            if side == "BUY":
                resp = self.rest.limit_order_gtc_buy(**params)
            else:
                resp = self.rest.limit_order_gtc_sell(**params)
            return True, resp
        except Exception as e:
            return False, e

    def _submit_market_order(self, product_id: str, side: str, quote_usd: float):
        client_order_id = f"ema-{product_id}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        params = {"client_order_id": client_order_id, "product_id": product_id}
        if self.portfolio_id:
            params["portfolio_id"] = self.portfolio_id
        try:
            if side == "BUY":
                params["quote_size"] = f"{quote_usd:.2f}"
                resp = self.rest.market_order_buy(**params)
            else:
                # SELL: use base_size and clamp to held position
                held = max(0.0, float(self.positions.get(product_id, 0.0)))
                if held <= 0:
                    return False, ValueError("No position to sell")
                last = float(self.quotes.get(product_id, {}).get("last") or 0.0)
                intended_base = quote_usd / last if last > 0 else held
                base_size = min(held, intended_base)
                b_dec = decimals_from_inc(self.base_inc.get(product_id, 1e-8))
                params["base_size"] = f"{base_size:.{b_dec}f}"
                resp = self.rest.market_order_sell(**params)
            return True, resp
        except Exception as e:
            return False, e

    def place_order(self, product_id: str, side: str, quote_usd: float, last_price: float):
        side = side.upper()
        assert side in {"BUY", "SELL"}

        display_qty = quote_usd / last_price if last_price > 0 else 0.0
        dry_run = bool(getattr(self.cfg, "dry_run", False))
        log_trade(product_id, side, quote_usd, last_price, display_qty, dry_run)

        if dry_run:
            if side == "BUY":
                self.session_cash_pnl -= quote_usd
                self.spend.add(quote_usd)  # BUY-only
            else:
                self.session_cash_pnl += quote_usd
            self.last.stamp(product_id)
            logging.info("[DRY RUN] %s %s $%.2f", side, product_id, quote_usd)
            return

        # Side-aware maker preference
        prefer_maker = bool(getattr(self.cfg, "prefer_maker", True))
        if side == "SELL":
            prefer_maker = bool(getattr(self.cfg, "prefer_maker_for_sells", prefer_maker))

        if prefer_maker:
            q = self.quotes.get(product_id, {})
            limit_price, base_size = compute_maker_limit(
                product_id=product_id,
                side=side,
                last_price=last_price,
                price_inc=self.price_inc.get(product_id, 0.01),
                base_inc=self.base_inc.get(product_id, 1e-8),
                usd_per_order=float(getattr(self.cfg, "usd_per_order", 1.0)),
                offset_bps=getattr(self.cfg, "maker_offset_bps_per_product", {}).get(
                    product_id, float(getattr(self.cfg, "maker_offset_bps", 5.0))
                ),
                bid=q.get("bid"),
                ask=q.get("ask"),
            )
            if side == "SELL":
                # clamp maker SELL size to held position
                held = max(0.0, float(self.positions.get(product_id, 0.0)))
                base_size = min(base_size, held)

            if base_size <= 0 or limit_price <= 0:
                logging.error("Invalid maker params for %s %s: price=%.8f size=%.8f", side, product_id, limit_price, base_size)
                return
            ok, resp = self._submit_limit_maker_order(product_id, side, base_size, limit_price)
        else:
            ok, resp = self._submit_market_order(product_id, side, quote_usd)

        if not ok:
            logging.error("%s order FAILED for %s $%.2f: %s", side, product_id, quote_usd, resp)
            return

        # BUY-only daily spend
        if side == "BUY":
            self.spend.add(quote_usd)
        self.last.stamp(product_id)

        try:
            body = getattr(resp, "to_dict", lambda: resp)()
            logging.info("Live %s %s $%.2f placed. Resp: %s", side, product_id, quote_usd, body)
        except Exception:
            logging.info("Live %s %s $%.2f placed.", side, product_id, quote_usd)

        # best-effort immediate fills -> update portfolio
        try:
            body = getattr(resp, "to_dict", lambda: resp)()
            order_id = (body.get("success_response", {}).get("order_id") or body.get("order_id"))

            # record intent snapshot for slippage/KPIs
            try:
                q = self.quotes.get(product_id, {})
                self._intent[str(order_id)] = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "product_id": product_id,
                    "side": side,
                    "intent_price": float(last_price) if last_price else None,
                    "bid": q.get("bid"),
                    "ask": q.get("ask"),
                }
            except Exception:
                pass

            if order_id:
                fills = self.rest.get(
                    "/api/v3/brokerage/orders/historical/fills",
                    params={"order_id": order_id, "limit": 100},
                )
                fb = getattr(fills, "to_dict", lambda: fills)()
                any_new = False
                fee_total = 0.0
                liq_flags = set()
                # Guard per-fill mutations + CSV to avoid races with reconcile thread
                with self._state_lock:
                    for f in fb.get("fills", []):
                        fp = self._fill_fingerprint(f)
                        if fp in self._processed_fills:
                            continue
                        side_f = (f.get("side") or f.get("order_side") or "").upper()
                        pid_f = f.get("product_id")
                        size_f = float(f.get("size") or f.get("base_size") or f.get("filled_size") or 0.0)
                        price_f = float(f.get("price") or 0.0)
                        fee_f = float(f.get("fee") or 0.0)
                        if side_f == "BUY":
                            new_qty = self.positions[pid_f] + size_f
                            new_cost = self.cost_basis[pid_f] * self.positions[pid_f] + (size_f * price_f) + fee_f
                            if new_qty > 0:
                                self.positions[pid_f] = new_qty
                                self.cost_basis[pid_f] = new_cost / new_qty
                        elif side_f == "SELL":
                            qty_before = self.positions[pid_f]
                            sell_qty = min(size_f, qty_before)
                            self.realized_pnl += sell_qty * (price_f - self.cost_basis[pid_f]) - fee_f
                            self.positions[pid_f] = max(0.0, qty_before - sell_qty)
                            if self.positions[pid_f] == 0.0:
                                self.cost_basis[pid_f] = 0.0
                        self._processed_fills[fp] = {"t": f.get("trade_time") or f.get("time")}
                        any_new = True
                        try:
                            fee_total += fee_f
                        except Exception:
                            pass
                        flag = f.get("liquidity_indicator")
                        if flag:
                            liq_flags.add(flag)

                        # KPI CSV logging (immediate)
                        try:
                            ts_iso = (f.get("trade_time") or f.get("time") or datetime.now(timezone.utc).isoformat())
                            oid = str(order_id) if order_id else None
                            intent = self._intent.get(oid, {})
                            intent_price = intent.get("intent_price")
                            pnl_fill = None
                            if side_f == "SELL":
                                try:
                                    sell_qty = min(size_f, self.positions.get(pid_f, 0.0) + size_f)
                                    pnl_fill = sell_qty * (price_f - self.cost_basis[pid_f]) - fee_f
                                except Exception:
                                    pnl_fill = None
                            hold_time_sec = None
                            try:
                                if side_f == "BUY":
                                    if (self.positions[pid_f] - size_f) <= 0 and self.positions[pid_f] > 0:
                                        self._entry_time[pid_f] = time.time()
                                elif side_f == "SELL":
                                    if self.positions[pid_f] == 0.0 and self._entry_time.get(pid_f):
                                        hold_time_sec = max(0.0, time.time() - self._entry_time[pid_f])
                                        self._entry_time[pid_f] = None
                            except Exception:
                                pass
                            self._append_trade_csv(ts_iso=ts_iso, order_id=oid, side=side_f, product_id=pid_f,
                                                   size=size_f, price=price_f, fee=fee_f,
                                                   liquidity=flag, pnl=pnl_fill,
                                                   position_after=self.positions.get(pid_f),
                                                   cost_basis_after=self.cost_basis.get(pid_f),
                                                   intent_price=intent_price, hold_time_sec=hold_time_sec)
                        except Exception as e:
                            logging.debug("CSV log (immediate) failed: %s", e)

                if any_new:
                    # prune if large
                    max_keys = int(getattr(self.cfg, "processed_fills_max", 10000))
                    if len(self._processed_fills) > max_keys:
                        drop_n = max(1, max_keys // 5)
                        for k in list(self._processed_fills.keys())[:drop_n]:
                            self._processed_fills.pop(k, None)
                    # Guard saves as well
                    with self._state_lock:
                        save_json(PROCESSED_FILLS_FILE, self._processed_fills)
                        self._save_portfolio()
                    run_delta = self.realized_pnl - self.run_pnl_baseline
                    pnl_str = f"{self.realized_pnl:.{PNL_DECIMALS}f}"
                    run_str = f"{run_delta:.{PNL_DECIMALS}f}"
                    logging.info(
                        "Updated from immediate fills. Fees: $%.2f | liquidity: %s | Lifetime P&L: $%s | This run: $%s",
                        fee_total, ",".join(sorted(liq_flags)) or "n/a", pnl_str, run_str
                    )
        except Exception as e:
            logging.debug("Could not fetch immediate fills: %s", e)

    # -------------------- misc --------------------
    def _fill_fingerprint(self, f: dict) -> str:
        oid = f.get("order_id", "")
        tid = f.get("trade_id") or f.get("fill_id") or f.get("sequence") or f.get("trade_time") or ""
        pid = f.get("product_id", "")
        sz = f.get("size") or f.get("base_size") or f.get("filled_size") or ""
        px = f.get("price") or ""
        fee = f.get("fee") or ""
        side = f.get("side") or f.get("order_side") or ""
        return f"{oid}|{tid}|{pid}|{sz}|{px}|{fee}|{side}"

    def log_session_pnl(self):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        live_run_delta = self.realized_pnl - self.run_pnl_baseline
        run_total = live_run_delta + self.session_cash_pnl
        life_str = f"{self.realized_pnl:.{PNL_DECIMALS}f}"
        run_str = f"{run_total:.{PNL_DECIMALS}f}"

        sep = "-" * 110 + "\n"
        duration = datetime.now(timezone.utc) - self.session_start

        with open(TRADE_LOG_FILE, "a") as f:
            f.write(f"{ts} | P&L this run: ${run_str} | Lifetime P&L: ${life_str}\n")
            f.write(sep)
            f.write(f"{ts} | Runtime duration: {duration}\n")
            f.write(sep)
            f.write("$" * 100 + "\n")

        logging.info("Session P&L logged: this run $%s | lifetime $%s | runtime %s", run_str, life_str, duration)

    # -------------------- fills reconciliation --------------------
    def reconcile_recent_fills(self, lookback_hours: int = 48):
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=lookback_hours)
        params = {
            "start_date": start.isoformat().replace("+00:00", "Z"),
            "end_date": end.isoformat().replace("+00:00", "Z"),
            "limit": 100,
        }
        try:
            resp = self.rest.get("/api/v3/brokerage/orders/historical/fills", params=params)
            fb = getattr(resp, "to_dict", lambda: resp)()
            fills = fb.get("fills", [])
        except Exception as e:
            logging.debug("Could not fetch recent fills: %s", e)
            return

        changed = False
        for f in fills:
            fp = self._fill_fingerprint(f)
            if fp in self._processed_fills:
                continue

            side = (f.get("side") or f.get("order_side") or "").upper()
            pid = f.get("product_id")
            if not pid or side not in {"BUY", "SELL"}:
                self._processed_fills[fp] = {"skip": True}
                changed = True
                continue

            try:
                size = float(f.get("size") or f.get("base_size") or f.get("filled_size") or 0.0)
                price = float(f.get("price") or 0.0)
                fee = float(f.get("fee") or 0.0)
            except Exception:
                self._processed_fills[fp] = {"bad_num": True}
                changed = True
                continue

            # Guard each fill’s state mutations + CSV with the lock
            with self._state_lock:
                if side == "BUY":
                    new_qty = self.positions[pid] + size
                    new_cost = self.cost_basis[pid] * self.positions[pid] + (size * price) + fee
                    if new_qty > 0:
                        self.positions[pid] = new_qty
                        self.cost_basis[pid] = new_cost / new_qty
                else:
                    qty_before = self.positions[pid]
                    sell_qty = min(size, qty_before)
                    pnl_add = sell_qty * (price - self.cost_basis[pid]) - fee
                    self.realized_pnl += pnl_add
                    self.positions[pid] = max(0.0, qty_before - sell_qty)
                    if self.positions[pid] == 0.0:
                        self.cost_basis[pid] = 0.0

                self._processed_fills[fp] = {"t": f.get("trade_time") or f.get("time")}

                # KPI CSV logging (reconcile)
                try:
                    ts_iso = (f.get("trade_time") or f.get("time") or datetime.now(timezone.utc).isoformat())
                    oid = f.get("order_id")
                    intent = self._intent.get(str(oid), {}) if oid else {}
                    intent_price = intent.get("intent_price")
                    pnl_fill = None
                    if side == "SELL":
                        try:
                            sell_qty = min(size, self.positions.get(pid, 0.0) + size)
                            pnl_fill = sell_qty * (price - self.cost_basis[pid]) - fee
                        except Exception:
                            pnl_fill = None
                    hold_time_sec = None
                    try:
                        if side == "BUY":
                            if (self.positions[pid] - size) <= 0 and self.positions[pid] > 0:
                                self._entry_time[pid] = time.time()
                        elif side == "SELL":
                            if self.positions[pid] == 0.0 and self._entry_time.get(pid):
                                hold_time_sec = max(0.0, time.time() - self._entry_time[pid])
                                self._entry_time[pid] = None
                    except Exception:
                        pass
                    self._append_trade_csv(ts_iso=ts_iso, order_id=str(oid) if oid else None, side=side, product_id=pid,
                                           size=size, price=price, fee=fee,
                                           liquidity=f.get("liquidity_indicator"), pnl=pnl_fill,
                                           position_after=self.positions.get(pid),
                                           cost_basis_after=self.cost_basis.get(pid),
                                           intent_price=intent_price, hold_time_sec=hold_time_sec)
                except Exception as e:
                    logging.debug("CSV log (reconcile) failed: %s", e)

            changed = True

        if changed:
            # prune if large
            max_keys = int(getattr(self.cfg, "processed_fills_max", 10000))
            if len(self._processed_fills) > max_keys:
                drop_n = max(1, max_keys // 5)
                for k in list(self._processed_fills.keys())[:drop_n]:
                    self._processed_fills.pop(k, None)
            # Guard final saves
            with self._state_lock:
                save_json(PROCESSED_FILLS_FILE, self._processed_fills)
                self._save_portfolio()
            run_delta = self.realized_pnl - (getattr(self, "run_pnl_baseline", self.realized_pnl))
            pnl_str = f"{self.realized_pnl:.{PNL_DECIMALS}f}"
            run_str = f"{run_delta:.{PNL_DECIMALS}f}"
            logging.info("Reconciled fills. Lifetime P&L: $%s | This run: $%s", pnl_str, run_str)

    def _on_candle_close(self, product_id: str, start_sec: int, close_price: float):
        # Update indicators once per closed candle
        s = self.short[product_id].update(close_price)
        l = self.long[product_id].update(close_price)
        if self.enable_advisors:
            self._macd[product_id].update(close_price)
            self._rsi[product_id].update(close_price)
        self.ticks[product_id] += 1  # now counts candles
        min_needed = self.min_ticks_per_product.get(product_id, int(getattr(self.cfg, "min_candles", getattr(self.cfg, "min_ticks", 60))))
        if self.ticks[product_id] >= min_needed and s is not None and l is not None:
            self.evaluate_signal(product_id, close_price, s, l)

    def reconcile_now(self, hours: Optional[int] = None) -> None:
        """Idempotent, reentrant-safe sweep of recent fills."""
        if getattr(self, "_reconciling", False):
            logging.debug("Reconcile already running; skipping.")
            return
        self._reconciling = True
        try:
            h = int(hours or getattr(self.cfg, "lookback_hours", 48))
            h = max(6, min(h, 48))  # gentle bounds
            self.reconcile_recent_fills(h)
        except Exception as e:
            logging.exception("reconcile_now failed: %s", e)
        finally:
            self._reconciling = False
