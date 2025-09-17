# bot/tradebot.py
import os
import sys
import json
import time
import uuid
import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv
from coinbase.rest import RESTClient
from coinbase.websocket import WSClient

from .config import BotConfig
from .indicators import RSI, MACD  # simple streaming indicators with .update(price)

# ------------------------------ #
# Constants / Files / Formatting #
# ------------------------------ #
PNL_DECIMALS = 8

STATE_DIR = Path(".state")
STATE_DIR.mkdir(exist_ok=True)
DAILY_FILE = STATE_DIR / "daily_spend.json"
LASTTRADE_FILE = STATE_DIR / "last_trades.json"
TRADE_LOG_FILE = STATE_DIR / "trade_log.txt"
PORTFOLIO_FILE = STATE_DIR / "portfolio.json"
PROCESSED_FILLS_FILE = STATE_DIR / "processed_fills.json"

# advisor thresholds (tweak later or move to config if you like)
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD   = 30.0
# MACD veto uses sign of histogram: >0 bullish momentum, <0 bearish momentum


# ------------------------------ #
# Small helpers                  #
# ------------------------------ #
def load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default

def save_json(path: Path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)

def log_trade(product_id: str, side: str, usd_amount: float, price: float, quantity: float, dry_run: bool):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    entry = (
        f"{ts} | {side:<4} {product_id:<10} "
        f"USD ${usd_amount:.2f} @ ${price:.6f} "
        f"Qty {quantity:.8f} "
        f"{'(DRY RUN)' if dry_run else ''}\n"
    )
    with open(TRADE_LOG_FILE, "a") as f:
        f.write(entry)


class SpendTracker:
    def __init__(self):
        self.data = load_json(DAILY_FILE, {})

    def _day_key(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def add(self, usd: float):
        k = self._day_key()
        self.data.setdefault(k, 0.0)
        self.data[k] += float(usd)
        save_json(DAILY_FILE, self.data)

    def today_total(self) -> float:
        return float(self.data.get(self._day_key(), 0.0))


class LastTradeTracker:
    def __init__(self):
        self.data = load_json(LASTTRADE_FILE, {})

    def ok(self, product_id: str, cooldown_sec: int) -> bool:
        t = self.data.get(product_id)
        if not t:
            return True
        return (time.time() - float(t)) >= cooldown_sec

    def stamp(self, product_id: str):
        self.data[product_id] = time.time()
        save_json(LASTTRADE_FILE, self.data)


class EMA:
    def __init__(self, period: int):
        self.period = max(1, int(period))
        self.mult = 2 / (self.period + 1)
        self.value: Optional[float] = None

    def update(self, price: float) -> float:
        if self.value is None:
            self.value = price
        else:
            self.value = (price - self.value) * self.mult + self.value
        return self.value


# ------------------------------ #
# TradeBot                       #
# ------------------------------ #
class TradeBot:
    def __init__(self, cfg: BotConfig, api_key: Optional[str] = None, api_secret: Optional[str] = None):
        self.cfg = cfg

        # API creds (allow env fallback)
        if not api_key or not api_secret:
            load_dotenv("APIkeys.env")
            api_key = api_key or os.getenv("COINBASE_API_KEY")
            api_secret = api_secret or os.getenv("COINBASE_API_SECRET")
        if not api_key or not api_secret:
            raise RuntimeError("Missing COINBASE_API_KEY / COINBASE_API_SECRET.")

        self.rest = RESTClient(api_key=api_key, api_secret=api_secret, rate_limit_headers=True)

        # WebSocket setup
        def on_msg(raw):
            try:
                msg = json.loads(raw)
            except Exception:
                logging.debug("Non-JSON WS message: %s", raw)
                return
            self.on_ws_message(msg)

        self.ws = WSClient(api_key=api_key, api_secret=api_secret, on_message=on_msg)

        # Per-product EMA and tick counters
        self.short: Dict[str, EMA] = {}
        self.long:  Dict[str, EMA] = {}
        self.ticks: Dict[str, int] = defaultdict(int)
        self.min_ticks_per_product: Dict[str, int] = {}

        # Advisors (per-product RSI & MACD)
        self.rsi: Dict[str, RSI] = {}
        self.macd: Dict[str, MACD] = {}

        for p in self.cfg.product_ids:
            se, le, mt = self._get_ema_params(p)
            self.short[p] = EMA(se)
            self.long[p]  = EMA(le)
            self.min_ticks_per_product[p] = mt
            # advisors: RSI(14), MACD(12,26,9) default
            self.rsi[p]  = RSI(period=14)
            self.macd[p] = MACD(fast=12, slow=26, signal=9)

        # Spend / cooldown
        self.spend = SpendTracker()
        self.last = LastTradeTracker()

        # Portfolio (fills-accurate)
        self.positions: Dict[str, float] = defaultdict(float)
        self.cost_basis: Dict[str, float] = defaultdict(float)
        self.realized_pnl = 0.0

        # P&L baselines
        self.run_pnl_baseline = 0.0
        self.session_cash_pnl = 0.0

        # Flags
        self.daily_cap_reached_logged = False
        self.stop_requested = False
        self.session_footer_written = False

        # Session start (for runtime)
        self.session_start = datetime.now(timezone.utc)

        # Coinbase product increments
        self.price_inc: Dict[str, float] = {}
        self.base_inc: Dict[str, float] = {}
        self._prime_increments()

        # Persisted state
        self._load_portfolio()
        self._load_fill_index()

    # ------------------------------ #
    # Config helpers                 #
    # ------------------------------ #
    def _get_ema_params(self, pid: str) -> Tuple[int, int, int]:
        ovr = self.cfg.ema_params_per_product.get(pid, {})
        se = int(ovr.get("short_ema", self.cfg.short_ema))
        le = int(ovr.get("long_ema",  self.cfg.long_ema))
        mt = int(ovr.get("min_ticks", self.cfg.min_ticks))
        return se, le, mt

    # ------------------------------ #
    # Persistence                    #
    # ------------------------------ #
    def _load_portfolio(self):
        data = load_json(PORTFOLIO_FILE, {"positions": {}, "cost_basis": {}, "realized_pnl": 0.0})
        for k, v in data.get("positions", {}).items():
            self.positions[k] = float(v)
        for k, v in data.get("cost_basis", {}).items():
            self.cost_basis[k] = float(v)
        self.realized_pnl = float(data.get("realized_pnl", 0.0))

    def _save_portfolio(self):
        save_json(PORTFOLIO_FILE, {
            "positions": self.positions,
            "cost_basis": self.cost_basis,
            "realized_pnl": float(self.realized_pnl),
        })

    def _load_fill_index(self):
        self._processed_fills = load_json(PROCESSED_FILLS_FILE, {})

    def _save_fill_index(self):
        save_json(PROCESSED_FILLS_FILE, self._processed_fills)

    # ------------------------------ #
    # Product increments             #
    # ------------------------------ #
    def _prime_increments(self):
        for pid in self.cfg.product_ids:
            try:
                prod = self.rest.get_product(product_id=pid)
                body = getattr(prod, "to_dict", lambda: {})()
                price_inc = body.get("price_increment") or body.get("quote_increment") or "0.01"
                base_inc  = body.get("base_increment") or "0.00000001"
                self.price_inc[pid] = float(price_inc)
                self.base_inc[pid]  = float(base_inc)
            except Exception:
                self.price_inc[pid] = 0.01
                self.base_inc[pid]  = 1e-8

    @staticmethod
    def _round_down_to_inc(value: float, inc: float) -> float:
        if inc <= 0:
            return value
        return (int(value / inc)) * inc

    @staticmethod
    def _round_up_to_inc(value: float, inc: float) -> float:
        if inc <= 0:
            return value
        steps = int((value + 1e-15) / inc)
        return (steps if abs(steps * inc - value) < 1e-12 else steps + 1) * inc

    @staticmethod
    def _decimals_from_inc(inc: float) -> int:
        s = f"{inc:.10f}".rstrip("0").rstrip(".")
        return len(s.split(".")[1]) if "." in s else 0

    # ------------------------------ #
    # Baseline P&L                  #
    # ------------------------------ #
    def set_run_baseline(self):
        self.run_pnl_baseline = self.realized_pnl
        base_str = f"{self.run_pnl_baseline:.{PNL_DECIMALS}f}"
        logging.info("P&L baseline set for this run: $%s", base_str)

    # ------------------------------ #
    # Fills reconciliation (live)    #
    # ------------------------------ #
    def _fill_fingerprint(self, f: dict) -> str:
        oid = f.get("order_id", "")
        tid = f.get("trade_id") or f.get("fill_id") or f.get("sequence") or f.get("trade_time") or ""
        pid = f.get("product_id", "")
        sz  = f.get("size") or f.get("base_size") or f.get("filled_size") or ""
        px  = f.get("price") or ""
        fee = f.get("fee") or ""
        side = f.get("side") or f.get("order_side") or ""
        return f"{oid}|{tid}|{pid}|{sz}|{px}|{fee}|{side}"

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
            pid  = f.get("product_id")
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
            changed = True

        if changed:
            self._save_fill_index()
            self._save_portfolio()
            run_delta = self.realized_pnl - (getattr(self, "run_pnl_baseline", self.realized_pnl))
            pnl_str = f"{self.realized_pnl:.{PNL_DECIMALS}f}"
            run_str = f"{run_delta:.{PNL_DECIMALS}f}"
            logging.info("Reconciled fills. Lifetime P&L: $%s | This run: $%s", pnl_str, run_str)

    # ------------------------------ #
    # Open/Close                     #
    # ------------------------------ #
    def open(self):
        self.ws.open()
        self.ws.ticker(product_ids=list(self.cfg.product_ids))
        logging.info("Subscribed to ticker for %s", ", ".join(self.cfg.product_ids))

    def close(self):
        if not self.session_footer_written:
            self.log_session_pnl()
            self.session_footer_written = True
        try:
            self.ws.close()
        except Exception:
            pass

    # ------------------------------ #
    # WebSocket handler              #
    # ------------------------------ #
    def on_ws_message(self, msg: dict):
        if msg.get("channel") != "ticker" or self.stop_requested:
            return
        events = msg.get("events") or []
        for ev in events:
            for t in ev.get("tickers", []):
                pid = t.get("product_id")
                price = t.get("price")
                if pid not in self.short:
                    # Safety: initialize on-the-fly if new symbol arrives
                    se, le, mt = self._get_ema_params(pid)
                    self.short[pid] = EMA(se)
                    self.long[pid]  = EMA(le)
                    self.min_ticks_per_product[pid] = mt
                    self.ticks[pid] = 0
                    self.rsi[pid] = RSI(period=14)
                    self.macd[pid] = MACD(fast=12, slow=26, signal=9)

                if price is None:
                    continue

                try:
                    p = float(price)
                except Exception:
                    continue

                # Update EMAs
                self.ticks[pid] += 1
                s = self.short[pid].update(p)
                l = self.long[pid].update(p)

                # Update advisors
                rsi_val = self.rsi[pid].update(p)
                macd_line, macd_signal, macd_hist = self.macd[pid].update(p)

                min_needed = self.min_ticks_per_product.get(pid, self.cfg.min_ticks)
                if self.ticks[pid] >= min_needed and s is not None and l is not None:
                    self.evaluate_signal(pid, p, s, l, rsi_val, macd_hist)

    # ------------------------------ #
    # Signal + Advisor gating        #
    # ------------------------------ #
    def evaluate_signal(self, product_id: str, price: float, s: float, l: float,
                        rsi_val: Optional[float], macd_hist: Optional[float]):
        if self.stop_requested:
            return

        # EMA captain
        signal = 1 if s > l else -1

        # Cooldown
        if not self.last.ok(product_id, self.cfg.cooldown_sec):
            return

        # SELL guardrails
        if signal < 0:
            if self.positions[product_id] <= 0.0:
                logging.info("Skip SELL %s: no position held.", product_id)
                return
            cb = self.cost_basis[product_id]
            if cb > 0.0 and price <= cb:
                logging.info("Skip SELL %s: price %.6f <= cost basis %.6f.", product_id, price, cb)
                return

        # Advisors (only if we have values)
        if rsi_val is not None and macd_hist is not None:
            if signal > 0:
                # BUY veto: RSI too hot or MACD momentum not supportive
                if rsi_val >= RSI_OVERBOUGHT or macd_hist <= 0:
                    logging.info("Advisor veto BUY %s (RSI=%.2f, MACD_hist=%.4f)", product_id, rsi_val, macd_hist)
                    return
            else:
                # SELL veto: RSI too cold or MACD momentum not supportive
                if rsi_val <= RSI_OVERSOLD or macd_hist >= 0:
                    logging.info("Advisor veto SELL %s (RSI=%.2f, MACD_hist=%.4f)", product_id, rsi_val, macd_hist)
                    return

        # Daily cap
        spent_today = self.spend.today_total()
        remaining = max(0.0, self.cfg.max_usd_per_day - spent_today)
        if remaining <= 0:
            if not self.daily_cap_reached_logged:
                logging.info("Daily cap reached (%.2f). Skipping trades.", self.cfg.max_usd_per_day)
                self.log_session_pnl()
                self.daily_cap_reached_logged = True
                self.session_footer_written = True
                self.stop_requested = True
            return

        notional = min(self.cfg.usd_per_order, remaining)
        try:
            if signal > 0:
                self.place_order(product_id, side="BUY",  quote_usd=notional, last_price=price)
            else:
                self.place_order(product_id, side="SELL", quote_usd=notional, last_price=price)
        except Exception as e:
            logging.exception("Order error for %s: %s", product_id, e)

    # ------------------------------ #
    # Maker limit computation        #
    # ------------------------------ #
    def _compute_maker_limit(self, product_id: str, side: str, last_price: float) -> Tuple[float, float]:
        offset_bps = self.cfg.maker_offset_bps_per_product.get(product_id, self.cfg.maker_offset_bps)
        offset = offset_bps / 10000.0
        price_inc = self.price_inc.get(product_id, 0.01)
        base_inc  = self.base_inc.get(product_id, 1e-8)

        if side == "BUY":
            raw_price = last_price * (1.0 - offset)
            limit_price = self._round_down_to_inc(raw_price, price_inc)
        else:
            raw_price = last_price * (1.0 + offset)
            limit_price = self._round_up_to_inc(raw_price, price_inc)

        base_size = max(0.0, self.cfg.usd_per_order / limit_price) if limit_price > 0 else 0.0
        base_size = self._round_down_to_inc(base_size, base_inc)
        return (limit_price, base_size)

    # ------------------------------ #
    # Order submission               #
    # ------------------------------ #
    def _submit_limit_maker_order(self, product_id: str, side: str, base_size: float, limit_price: float):
        client_order_id = f"ema-{product_id}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        p_dec = self._decimals_from_inc(self.price_inc.get(product_id, 0.01))
        b_dec = self._decimals_from_inc(self.base_inc.get(product_id, 1e-8))
        limit_price_str = f"{limit_price:.{p_dec}f}"
        base_size_str   = f"{base_size:.{b_dec}f}"

        params = {
            "client_order_id": client_order_id,
            "product_id": product_id,
            "limit_price": limit_price_str,
            "base_size": base_size_str,
            "post_only": True,
        }
        if self.cfg.portfolio_id:
            params["portfolio_id"] = self.cfg.portfolio_id

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
        params = {"client_order_id": client_order_id, "product_id": product_id, "quote_size": f"{quote_usd:.2f}"}
        if self.cfg.portfolio_id:
            params["portfolio_id"] = self.cfg.portfolio_id
        try:
            if side == "BUY":
                resp = self.rest.market_order_buy(**params)
            else:
                resp = self.rest.market_order_sell(**params)
            return True, resp
        except Exception as e:
            return False, e

    def place_order(self, product_id: str, side: str, quote_usd: float, last_price: float):
        side = side.upper()
        assert side in {"BUY", "SELL"}

        # Local log (informational)
        display_qty = quote_usd / last_price if last_price > 0 else 0.0
        log_trade(product_id, side, quote_usd, last_price, display_qty, self.cfg.dry_run)

        # DRY RUN
        if self.cfg.dry_run:
            if side == "BUY":
                self.session_cash_pnl -= quote_usd
            else:
                self.session_cash_pnl += quote_usd
            self.spend.add(quote_usd)
            self.last.stamp(product_id)
            logging.info("[DRY RUN] %s %s $%.2f", side, product_id, quote_usd)
            return

        # LIVE
        if self.cfg.prefer_maker:
            limit_price, base_size = self._compute_maker_limit(product_id, side, last_price)
            if base_size <= 0 or limit_price <= 0:
                logging.error("Invalid maker params for %s %s: price=%.8f size=%.8f",
                              side, product_id, limit_price, base_size)
                return
            ok, resp = self._submit_limit_maker_order(product_id, side, base_size, limit_price)
        else:
            ok, resp = self._submit_market_order(product_id, side, quote_usd)

        if not ok:
            logging.error("%s order FAILED for %s $%.2f: %s", side, product_id, quote_usd, resp)
            return

        # Count toward cap & cooldown on submission
        self.spend.add(quote_usd)
        self.last.stamp(product_id)

        # Log response
        try:
            body = getattr(resp, "to_dict", lambda: resp)()
            logging.info("Live %s %s $%.2f placed. Resp: %s", side, product_id, quote_usd, body)
        except Exception:
            logging.info("Live %s %s $%.2f placed.", side, product_id, quote_usd)

        # Try to fetch fills immediately (best-effort)
        try:
            body = getattr(resp, "to_dict", lambda: resp)()
            order_id = (body.get("success_response", {}).get("order_id") or body.get("order_id"))
            if order_id:
                fills = self.rest.get(
                    "/api/v3/brokerage/orders/historical/fills",
                    params={"order_id": order_id, "limit": 100},
                )
                fb = getattr(fills, "to_dict", lambda: fills)()
                any_new = False
                fee_total = 0.0
                liq_flags = set()
                for f in fb.get("fills", []):
                    fp = self._fill_fingerprint(f)
                    if fp in self._processed_fills:
                        continue
                    side_f = (f.get("side") or f.get("order_side") or "").upper()
                    pid_f  = f.get("product_id")
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

                if any_new:
                    self._save_fill_index()
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

    # ------------------------------ #
    # Footer                         #
    # ------------------------------ #
    def log_session_pnl(self):
        """Append P&L and runtime duration at end of session."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        live_run_delta = self.realized_pnl - self.run_pnl_baseline
        run_total = live_run_delta + self.session_cash_pnl
        life_str = f"{self.realized_pnl:.{PNL_DECIMALS}f}"
        run_str = f"{run_total:.{PNL_DECIMALS}f}"

        sep = "-" * 117 + "\n"
        duration = datetime.now(timezone.utc) - self.session_start

        with open(TRADE_LOG_FILE, "a") as f:
            f.write(f"{ts} | P&L this run: ${run_str} | Lifetime P&L: ${life_str}\n")
            f.write(sep)
            f.write(f"{ts} | Runtime duration: {duration}\n")
            f.write(sep)
            f.write("$" * 100 + "\n")

        logging.info("Session P&L logged: this run $%s | lifetime $%s | runtime %s", run_str, life_str, duration)
