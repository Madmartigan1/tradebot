# bot/tradebot.py
import json
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Tuple

from coinbase.rest import RESTClient
from coinbase.websocket import WSClient

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

        # REST/WS
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
        self.ticks: Dict[str, int] = defaultdict(int)
        self.min_ticks_per_product: Dict[str, int] = {}
        self._trend = defaultdict(int)  # -1 below, 0 band, +1 above (for crossover events)

        # Advisors
        self.enable_advisors: bool = bool(getattr(self.cfg, "enable_advisors", False))
        self._rsi = {p: RSI(period=int(getattr(self.cfg, "rsi_period", 14))) for p in self.product_ids}
        self._macd = {
            p: MACD(
                fast=int(getattr(self.cfg, "macd_fast", 12)),
                slow=int(getattr(self.cfg, "macd_slow", 26)),
                signal=int(getattr(self.cfg, "macd_signal", 9)),
            )
            for p in self.product_ids
        }
        self.advisor_settings = AdvisorSettings(
            enable_rsi=self.enable_advisors,
            rsi_period=int(getattr(self.cfg, "rsi_period", 14)),
            rsi_buy_min=float(getattr(self.cfg, "rsi_buy_floor", 30.0)),
            rsi_buy_max=float(getattr(self.cfg, "rsi_sell_ceiling", 70.0)),
            rsi_sell_min=float(getattr(self.cfg, "rsi_buy_floor", 30.0)),
            rsi_sell_max=float(getattr(self.cfg, "rsi_sell_ceiling", 70.0)),
            enable_macd=self.enable_advisors,
            macd_fast=int(getattr(self.cfg, "macd_fast", 12)),
            macd_slow=int(getattr(self.cfg, "macd_slow", 26)),
            macd_signal=int(getattr(self.cfg, "macd_signal", 9)),
            normalize_macd=True,
            macd_buy_min=0.0,
            macd_sell_max=0.0,
        )

        # EMA objects and per-product minimum ticks
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

        # Increments (price/base)
        self.price_inc: Dict[str, float] = {}
        self.base_inc: Dict[str, float] = {}
        self._prime_increments()

        # Quote cache (for maker pricing)
        self.quotes = defaultdict(lambda: {"bid": None, "ask": None, "last": None})

        # Load persisted portfolio + processed fills
        self._load_portfolio()
        self._processed_fills = load_json(PROCESSED_FILLS_FILE, {})

    # -------------------- helpers & config --------------------
    def _get_ema_params(self, pid: str) -> Tuple[int, int, int]:
        ovr = getattr(self.cfg, "ema_params_per_product", {}).get(pid, {})
        se = int(ovr.get("short_ema", getattr(self.cfg, "short_ema", 20)))
        le = int(ovr.get("long_ema", getattr(self.cfg, "long_ema", 50)))
        mt = int(ovr.get("min_ticks", getattr(self.cfg, "min_ticks", 60)))
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

    # rounding helpers now from orders.py via decimals_from_inc

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
            # prune if large
            max_keys = int(getattr(self.cfg, "processed_fills_max", 10000))
            if len(self._processed_fills) > max_keys:
                drop_n = max(1, max_keys // 5)
                for k in list(self._processed_fills.keys())[:drop_n]:
                    self._processed_fills.pop(k, None)

            save_json(PROCESSED_FILLS_FILE, self._processed_fills)
            self._save_portfolio()
            run_delta = self.realized_pnl - (getattr(self, "run_pnl_baseline", self.realized_pnl))
            pnl_str = f"{self.realized_pnl:.{PNL_DECIMALS}f}"
            run_str = f"{run_delta:.{PNL_DECIMALS}f}"
            logging.info("Reconciled fills. Lifetime P&L: $%s | This run: $%s", pnl_str, run_str)

    def open(self):
        self.ws.open()
        self.ws.ticker(product_ids=self.product_ids)
        logging.info("Subscribed to ticker for %s", ", ".join(self.product_ids))

    def close(self):
        if not self.session_footer_written:
            self.log_session_pnl()
            self.session_footer_written = True
        try:
            self.ws.close()
        except Exception:
            pass

    # -------------------- websocket --------------------
    def on_ws_message(self, msg: dict):
        if msg.get("channel") != "ticker" or self.stop_requested:
            return
        events = msg.get("events") or []
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

                # Update indicators
                self.ticks[pid] += 1
                s = self.short[pid].update(p)
                l = self.long[pid].update(p)

                if self.enable_advisors:
                    self._macd[pid].update(p)
                    self._rsi[pid].update(p)

                min_needed = self.min_ticks_per_product.get(pid, int(getattr(self.cfg, "min_ticks", 60)))
                if self.ticks[pid] >= min_needed and s is not None and l is not None:
                    self.evaluate_signal(pid, p, s, l)

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

        prev = self._trend[product_id]
        if rel == 0 or rel == prev:
            return  # no new crossover event
        self._trend[product_id] = rel
        signal = rel  # +1 buy crossover, -1 sell crossover

        if not self.last.ok(product_id, int(getattr(self.cfg, "cooldown_sec", 300))):
            return

        # SELL guardrails: must hold position
        if signal < 0 and self.positions[product_id] <= 0.0:
            logging.info("Skip SELL %s: no position held.", product_id)
            return

        # Cost-basis & stop-loss tolerance for SELLs
        if signal < 0:
            cb = self.cost_basis[product_id]
            if cb > 0.0:
                if getattr(self.cfg, "max_loss_bps", None) is not None:
                    tol = float(getattr(self.cfg, "max_loss_bps")) / 10_000.0
                    floor = cb * (1.0 - tol)
                    if price < floor:
                        logging.info(
                            "Skip SELL %s: price %.6f < stop-loss floor %.6f (cb %.6f, tol %.2f bps).",
                            product_id, price, floor, cb, float(getattr(self.cfg, "max_loss_bps")),
                        )
                        return
                elif price <= cb:
                    logging.info("Skip SELL %s: price %.6f <= cost basis %.6f.", product_id, price, cb)
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

        # Daily cap
        spent_today = self.spend.today_total()
        remaining = max(0.0, float(getattr(self.cfg, "max_usd_per_day", 10.0)) - spent_today)
        if remaining <= 0:
            if not self.daily_cap_reached_logged:
                logging.info("Daily cap reached (%.2f). Skipping trades.", float(getattr(self.cfg, "max_usd_per_day", 10.0)))
                self.log_session_pnl()
                self.daily_cap_reached_logged = True
                self.session_footer_written = True
                self.stop_requested = True
            return

        notional = min(float(getattr(self.cfg, "usd_per_order", 1.0)), remaining)
        try:
            side = "BUY" if signal > 0 else "SELL"
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
        params = {"client_order_id": client_order_id, "product_id": product_id, "quote_size": f"{quote_usd:.2f}"}
        if self.portfolio_id:
            params["portfolio_id"] = self.portfolio_id
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

        display_qty = quote_usd / last_price if last_price > 0 else 0.0
        dry_run = bool(getattr(self.cfg, "dry_run", False))
        log_trade(product_id, side, quote_usd, last_price, display_qty, dry_run)

        if dry_run:
            if side == "BUY":
                self.session_cash_pnl -= quote_usd
            else:
                self.session_cash_pnl += quote_usd
            self.spend.add(quote_usd)
            self.last.stamp(product_id)
            logging.info("[DRY RUN] %s %s $%.2f", side, product_id, quote_usd)
            return

        prefer_maker = bool(getattr(self.cfg, "prefer_maker", True))
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
            if base_size <= 0 or limit_price <= 0:
                logging.error("Invalid maker params for %s %s: price=%.8f size=%.8f", side, product_id, limit_price, base_size)
                return
            ok, resp = self._submit_limit_maker_order(product_id, side, base_size, limit_price)
        else:
            ok, resp = self._submit_market_order(product_id, side, quote_usd)

        if not ok:
            logging.error("%s order FAILED for %s $%.2f: %s", side, product_id, quote_usd, resp)
            return

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

                if any_new:
                    # prune if large
                    max_keys = int(getattr(self.cfg, "processed_fills_max", 10000))
                    if len(self._processed_fills) > max_keys:
                        drop_n = max(1, max_keys // 5)
                        for k in list(self._processed_fills.keys())[:drop_n]:
                            self._processed_fills.pop(k, None)

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
