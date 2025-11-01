# bot/tradebot.py — v1.1.4
# Adds:
#  - Quartermaster exits (x% take-profit; time-in-trade stagnation)
#  - Reason tagging for orders → trades.csv (entry_reason/exit_reason)
#  - Non-invasive pre-check before EMA captain logic
#  - Quartermaster looping safeguard
#  - Watchdog used to monitor connectivity 

import json
import logging
import time
import uuid
import csv
import threading
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Tuple
from .utils import RestProxy
from coinbase.rest import RESTClient
from coinbase.websocket import WSClient


# --- Candle helpers ---
class CandleBuilder:
    """Simple per-coin OHLCV builder for fixed-second buckets from ticker events."""
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

def _to_iso(ts_epoch: int | float) -> str:
    """Epoch seconds → ISO-8601 UTC (…Z) for Coinbase REST candles."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(float(ts_epoch), tz=timezone.utc).isoformat().replace("+00:00","Z")

def _align_to_bucket(ts_epoch: int | float, bucket_sec: int) -> int:
    """Floor timestamp to the start of its candle bucket."""
    b = int(bucket_sec) if bucket_sec and bucket_sec > 0 else 60
    t = int(ts_epoch)
    return t - (t % b)

def _granularity_enum(gran_sec: int) -> str:
    mapping = {
        60: "ONE_MINUTE", 300: "FIVE_MINUTE", 900: "FIFTEEN_MINUTE", 1800: "THIRTY_MINUTE",
        3600: "ONE_HOUR", 7200: "TWO_HOUR", 14400: "FOUR_HOUR", 21600: "SIX_HOUR", 86400: "ONE_DAY",
    }
    return mapping.get(int(gran_sec), "FIVE_MINUTE")

# --- Quartermaster helpers ---

def _profit_bps(entry_price: float, last_price: float) -> float:
    if not entry_price or not last_price or entry_price <= 0 or last_price <= 0:
        return 0.0
    return (last_price / entry_price - 1.0) * 10_000.0


def _quartermaster_exit_ok(cfg, last_price: float, entry_price: float,
                           hold_hours: float, macd_hist: float | None) -> tuple[bool, str]:
    """
    Returns (should_exit, reason): reason ∈ {"take_profit","stagnation"}
    """
    if not getattr(cfg, "enable_quartermaster", True):
        return False, ""

    pbps = _profit_bps(entry_price, last_price)

    # 1) Take-profit band (default 1200 bps = 12%)
    if pbps >= float(getattr(cfg, "take_profit_bps", 1200)):
        if getattr(cfg, "quartermaster_respect_macd", True):
            flat_max = float(getattr(cfg, "flat_macd_abs_max", 0.40))
            if macd_hist is not None and macd_hist > flat_max:
                # still strong up momentum → let EMA captain handle it
                return False, ""
        return True, "take_profit"

    # 2) Time-in-trade stagnation cull
    if hold_hours >= float(getattr(cfg, "max_hold_hours", 48)):
        if abs(pbps) < float(getattr(cfg, "stagnation_close_bps", 200)):
            flat_max = float(getattr(cfg, "flat_macd_abs_max", 0.40))
            if macd_hist is None or abs(macd_hist) <= flat_max:
                return True, "stagnation"

    return False, ""


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
from .orders import compute_maker_limit, decimals_from_inc, round_down_to_inc
from .strategy import AdvisorSettings, advisor_allows

class TradeBot:
    def __init__(self, cfg, api_key: str, api_secret: str, portfolio_id: Optional[str] = None):
        self.cfg = cfg
        # live-balance cache (per base asset)
        self._acct_cache: Dict[str, dict] = {}  # base -> {"ts": epoch, "avail": float}
        # Local-candle settle queue: (release_ts, coin_id, start_sec, close_price)
        self._local_settle_q = deque()
        # default settle delay (ms); override via CONFIG.local_close_settle_ms if present
        self._local_settle_ms = int(getattr(self.cfg, "local_close_settle_ms", 150))
        self.portfolio_id = portfolio_id if portfolio_id is not None else getattr(cfg, "portfolio_id", None)
        self._balances: Dict[str, float] = {}
        
        # Core per-coin state
        self.ticks: Dict[str, int] = defaultdict(int)  # counts candles in candle mode
        self.min_ticks_per_coin: Dict[str, int] = {}
        self._trend = defaultdict(int)   # -1 below, 0 band, +1 above
        self._primed = set()             # first crossover primes only (no trade)
        self._pending = {}               # coin_id -> {"rel": +/-1/0, "count": N}
        self._qm_last_ts = defaultdict(float)  # last time Quartermaster SELL fired per coin
        self._qm_dust_suppress_until = defaultdict(float)  # suppress dust exits until epoch time
        
        # Thread locking to prevent races in state + CSV writes
        self._state_lock = threading.RLock()

        # REST/WS
        self.api_key = api_key
        self.api_secret = api_secret
        
        # Base REST client
        self.rest = RESTClient(api_key=api_key, api_secret=api_secret, rate_limit_headers=True)
        # Wrap REST with retry+pacing proxy
        try:
            self.rest = RestProxy(
                self.rest,
                attempts=int(getattr(cfg, "rest_retry_attempts", 3)),
                backoff_min_ms=int(getattr(cfg, "rest_retry_backoff_min_ms", 200)),
                backoff_max_ms=int(getattr(cfg, "rest_retry_backoff_max_ms", 600)),
                retry_statuses=list(getattr(cfg, "rest_retry_on_status", [429,500,502,503,504])),
                rps_soft_limit=float(getattr(cfg, "rest_rps_soft_limit", 8.0)),
            )
        except Exception:
            pass
            
        # Initialize WS client/handler
        self._init_ws()
        
    def _set_local_mode_grace(self, buckets: float = 1.2):
        """
        Give each coin a grace window after switching to LOCAL aggregation so the
        stall watchdog does not thrash while the first local candle is forming.
        'buckets' is a multiplier of granularity_sec.
        """
        horizon = time.time() + max(1.0, float(self.granularity_sec) * float(buckets))
        for pid in self.coin_ids:
            self._stall_grace_until[pid] = horizon
        logging.info("Stall watchdog grace set for all coins until %.0f (%.0fs).", horizon, horizon - time.time())
        
    def _flush_local_settle(self, now_ts: float | None = None):
        """Emit any settled local candles whose release time has arrived."""
        if self.candle_mode != "local":
            return
        if not self._local_settle_q:
            return
        if now_ts is None:
            now_ts = time.time()
        while self._local_settle_q and self._local_settle_q[0][0] <= now_ts:
            _, pid, start, close = self._local_settle_q.popleft()
            try:
                self._on_candle_close(pid, start, close)
            except Exception as e:
                logging.debug("Local settle flush failed for %s: %s", pid, e)
        return
        
    # -------------------------
    # Websocket & runtime state
    # -------------------------
    def _init_ws(self):
        def on_msg(raw):
            try:
                msg = json.loads(raw)
            except Exception:
                logging.debug("Non-JSON WS message: %s", raw)
                return
            try:
                self.on_ws_message(msg)
            except Exception as e:
                # Never let a logging typo or handler bug kill the WS reader
                logging.exception("WS handler error: %s (frame=%s)", e, str(msg)[:500])
        self.ws = WSClient(api_key=self.api_key, api_secret=self.api_secret, on_message=on_msg)

        
        # Coins
        self.coin_ids = list(getattr(self.cfg, "coin_ids", []))
        if not self.coin_ids:
            raise ValueError("No coin_ids provided in config.")

        # Indicators and per-coin params
        self.short: Dict[str, EMA] = {}
        self.long: Dict[str, EMA] = {}

        # Advisors
        self.enable_advisors: bool = bool(
            getattr(self.cfg, "enable_advisors", getattr(self.cfg, "use_advisors", False))
        )
        self._rsi = {p: RSI(period=int(getattr(self.cfg, "rsi_period", 14))) for p in self.coin_ids}
        self._macd = {
            p: MACD(
                fast=int(getattr(self.cfg, "macd_fast", 12)),
                slow=int(getattr(self.cfg, "macd_slow", 26)),
                signal=int(getattr(self.cfg, "macd_signal", 9)),
            )
            for p in self.coin_ids
        }
    
        # One-sided RSI/MACD veto settings
        self.advisor_settings = AdvisorSettings(
            enable_rsi=self.enable_advisors,
            rsi_period=int(getattr(self.cfg, "rsi_period", 14)),
            rsi_buy_min=0.0,
            rsi_buy_max=float(getattr(self.cfg, "rsi_buy_max", getattr(self.cfg, "rsi_sell_ceiling", 70.0))),
            rsi_sell_min=float(getattr(self.cfg, "rsi_sell_min", getattr(self.cfg, "rsi_buy_floor", 30.0))),
            rsi_sell_max=100.0,
            enable_macd=self.enable_advisors,
            macd_fast=int(getattr(self.cfg, "macd_fast", 12)),
            macd_slow=int(getattr(self.cfg, "macd_slow", 26)),
            macd_signal=int(getattr(self.cfg, "macd_signal", 9)),
            normalize_macd=True,
            macd_buy_min=float(getattr(self.cfg, "macd_buy_min", 0.0)),
            macd_sell_max=float(getattr(self.cfg, "macd_sell_max", 0.0)),
        )

        # Log current settings snapshot
        cur_conf = int(getattr(self.cfg, "confirm_candles", getattr(self.cfg, "confirm_ticks", 2)))
        logging.info(
            "Advisors: RSI buy<=%.1f / sell>=%.1f (period=%d) | MACD %d/%d/%d, thresholds buy>=%.2f bps sell<=%.2f bps | "
            "deadband=%.2f bps | confirm=%d",
            self.advisor_settings.rsi_buy_max, self.advisor_settings.rsi_sell_min, self.advisor_settings.rsi_period,
            self.advisor_settings.macd_fast, self.advisor_settings.macd_slow, self.advisor_settings.macd_signal,
            self.advisor_settings.macd_buy_min, self.advisor_settings.macd_sell_max,
            float(getattr(self.cfg, "ema_deadband_bps", 0.0)),
            cur_conf,
        )

        # Snapshot QM config so runs are self-documenting
        logging.info(
            "Quartermaster: take_profit_bps=%s | max_hold_hours=%s | respect_macd=%s "
            "| flat_macd_abs_max=%s | shave_steps=%s | exits=MARKET_ONLY",
            getattr(self.cfg, "take_profit_bps", 800),
            getattr(self.cfg, "max_hold_hours", 48),
            getattr(self.cfg, "quartermaster_respect_macd", True),
            getattr(self.cfg, "flat_macd_abs_max", 0.40),
            getattr(self.cfg, "full_exit_shave_increments", 1),
        )

        # -------- Candle config / state --------
        self.candle_mode = str(getattr(self.cfg, "mode", getattr(self.cfg, "candle_mode", "ws"))).lower()
        ci = str(getattr(self.cfg, "candle_interval", "")).lower().strip()
        _ci2sec = {"1m":60,"5m":300,"15m":900,"30m":1800,"1h":3600,"2h":7200,"4h":14400,"6h":21600,"1d":86400}
        self.granularity_sec = int(_ci2sec.get(ci, int(getattr(self.cfg, "granularity_sec", 300))))

        self._last_candle_start: Dict[str, int | None] = {p: None for p in self.coin_ids}
        self._cur_candle_close: Dict[str, float | None] = {p: None for p in self.coin_ids}
        self._builders: Dict[str, CandleBuilder] = {p: CandleBuilder(self.granularity_sec) for p in self.coin_ids}
        
        # Visibility: make candle mode explicit in logs
        logging.info("Candle mode: %s (interval=%ds)", self.candle_mode.upper(), self.granularity_sec)
        
        self._live_bal_ttl = int(getattr(self.cfg, "live_balance_ttl_s", 20))
        
        # EMA objects and per-coin minimum candles
        for p in self.coin_ids:
            se, le, mt = self._get_ema_params(p)
            self.short[p] = EMA(se)
            self.long[p] = EMA(le)
            self.min_ticks_per_coin[p] = mt

        # --- Candle-stall watchdog state ---
        # Last observed *closed* candle time per coin (epoch seconds).
        self._last_candle_close_ts: Dict[str, float] = {p: 0.0 for p in self.coin_ids}
        
        # Consider stalled if no close for N * granularity_sec (configurable).
        self._stall_candle_factor = int(getattr(self.cfg, "stall_candle_factor", 3))
        
        # Escalation counters across the session.
        self._stall_hits = 0          # consecutive stalls (resubscribe → reconnect)
        self._stall_total = 0         # lifetime stalls (optional flip-to-local threshold)
        
        # Skip stall checks for coins under timed grace (e.g., after flipping to LOCAL)
        self._stall_grace_until: Dict[str, float] = {p: 0.0 for p in self.coin_ids}
        self._consecutive_stall_windows = 0
        self._last_stall_action_ts = 0.0
        
        # Spend/cooldown
        self.spend = SpendTracker()
        self.last = LastTradeTracker()
        logging.info(
            "Spend cap=$%.2f | spent today=$%.2f",
            float(getattr(self.cfg, "daily_spend_cap_usd", 0.0)),
            self.spend.today_total(),
        )
        
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
        self._last_ping_ts = 0.0
        self._last_subscribe_ts = time.time()
        self._idle_reconnects = 0
        # Serialize WS reconnect/subscribe paths to avoid racey double actions
        self._ws_lock = threading.RLock()
        # Telemetry/heartbeat
        self._last_heartbeat_ts = 0.0
        self._last_rest_backstop_poll_ts = 0.0  # throttle REST backstop polling
        # Counts candles closed since last heartbeat (rolled up in HB log)
        self._candles_closed = 0
        # Reconnect backoff bounds (seconds)
        self._ws_backoff_max = int(getattr(self.cfg, "ws_reconnect_backoff_max", 60))
        self._intent = {}  # order_id -> intent metadata (price at signal etc.)
        self._entry_time = defaultdict(lambda: None)  # per-coin entry timestamp when pos goes 0 -> >0

        # Increments (price/base)
        self.price_inc: Dict[str, float] = {}
        self.base_inc: Dict[str, float] = {}
        self.min_market_base_size: Dict[str, float] = {}
        self._prime_increments()

        # Quote cache (for maker pricing)
        self.quotes = defaultdict(lambda: {"bid": None, "ask": None, "last": None})

        # Load persisted portfolio + processed fills
        self._load_portfolio()
        from .persistence import ProcessedFills
        self._processed_fills = ProcessedFills(load_json(PROCESSED_FILLS_FILE, {}))

        # Clamp/seed AFTER loading portfolio (so we don't get overwritten)
        for pid in self.coin_ids:
            try:
                live = float(self._get_live_available_base(pid))
            except Exception:
                live = -1.0
            if live >= 0.0:
                cache = float(self.positions.get(pid, 0.0))
                if cache <= 0.0 and live > 0.0:
                    self.positions[pid] = live        # seed cache from live
                else:
                    self.positions[pid] = min(cache, live)   # never exceed live

        # Optional: backfill indicators from REST candles to warm up
        if bool(getattr(self.cfg, "use_backfill", True)):
            logging.info("Seeding indicators from REST candles (warmup_candles=%d, interval=%ds)…",
                         int(getattr(self.cfg, "warmup_candles", 200)), self.granularity_sec)
            try:
                self._backfill_seed_indicators()
            except Exception as e:
                logging.debug("Backfill seeding failed: %s", e)
    
    # -------------------------
    # API response normalization
    # -------------------------
    def _resp_ok(self, resp) -> bool:
        """Return True only if the Coinbase API indicates a successful accept.
        Tolerates SDK objects or raw dicts."""
        try:
            body = getattr(resp, "to_dict", lambda: resp)() or {}
        except Exception:
            return False
        # Hard failure if explicit error is present
        if body.get("success") is False:
            return False
        if body.get("error_response"):
            return False
        # Treat presence of success_response or order_id as success
        if body.get("success_response") or body.get("order_id") or body.get("success") is True:
            return True
        # Otherwise, be conservative
        return False
    
    # NOTE: now uses a short TTL cache and graceful fallback on 5xx server errors       
    def _get_live_available_base(self, coin_id: str) -> float:
        """
        Prefer Advanced Trade 'trading-available' over generic funding 'available';
        subtract any hold; paginate; pass portfolio_id; optional fallback to local position.
        """
        base = coin_id.split("-")[0]
        now = time.time()
        # 1) Serve from cache if fresh
        try:
            c = self._acct_cache.get(base)
            if c and (now - float(c.get("ts", 0.0))) <= max(5, self._live_bal_ttl):
                return float(c.get("avail", 0.0))
        except Exception:
            pass
        try:
            params = {}
            if getattr(self, 'portfolio_id', None):
                params["portfolio_id"] = self.portfolio_id
            cursor = None
            while True:
                if cursor:
                    params["cursor"] = cursor
                resp = self.rest.get_accounts(**params)
                body = getattr(resp, "to_dict", lambda: resp)() or {}
                for acc in body.get("accounts", []):
                    cur = acc.get("currency") or acc.get("asset")
                    if cur != base:
                        continue
                    trad = acc.get("trading_available_balance") or acc.get("available_for_trading")
                    val = trad.get("value") if isinstance(trad, dict) else trad
                    if val is None:
                        avail = acc.get("available_balance") or acc.get("available")
                        val = avail.get("value") if isinstance(avail, dict) else avail
                    try:
                        val_f = float(val or 0.0)
                    except Exception:
                        val_f = 0.0
                    hold = acc.get("hold") or acc.get("hold_balance") or 0.0
                    try:
                        hold_f = float(hold.get("value")) if isinstance(hold, dict) else float(hold or 0.0)
                    except Exception:
                        hold_f = 0.0
                    avail_f = max(0.0, val_f - hold_f)
                    # update cache
                    try:
                        self._acct_cache[base] = {"ts": now, "avail": float(avail_f)}
                    except Exception:
                        pass
                    return avail_f
                cursor = (
                    body.get("cursor") or body.get("next_cursor")
                    or (body.get("pagination") or {}).get("cursor")
                )
                if not cursor:
                    break
        except Exception as e:
            logging.debug("get_accounts failed while reading live available for %s: %s", coin_id, e)

        # 2) Graceful fallback path on errors/5xx: use cached value if present
        try:
            c = self._acct_cache.get(base)
            if c:
                return float(c.get("avail", 0.0))
        except Exception:
            pass

        if bool(getattr(self.cfg, "allow_position_fallback_for_avail", False)):
            return max(0.0, float(self.positions.get(coin_id, 0.0)))
        return 0.0

    def _get_ema_params(self, coin_id: str) -> Tuple[int, int, int]:
        """
        Returns (short_ema, long_ema, min_ticks_needed) for a given coin.

        - Uses CONFIG.short_ema / CONFIG.long_ema by default.
        - If CONFIG.ema_params_per_coin has an entry for coin_id, it can
          override with keys: {"short": int, "long": int} or {"short_ema","long_ema"}.
        - min_ticks_needed ensures indicators are warmed up before trading:
          max(long_ema + confirm_candles, CONFIG.min_candles).
        """
        # --- global defaults ---
        se = int(getattr(self.cfg, "short_ema", 40))
        le = int(getattr(self.cfg, "long_ema", 120))

        # --- per-coin override (optional) ---
        per = getattr(self.cfg, "ema_params_per_coin", {})
        if isinstance(per, dict):
            ov = per.get(coin_id) or {}
            if isinstance(ov, dict):
                se = int(ov.get("short", ov.get("short_ema", se)))
                le = int(ov.get("long",  ov.get("long_ema",  le)))

        # guardrails
        if se <= 0: se = 40
        if le <= 1: le = max(120, se + 1)
        if se >= le:  # keep short < long
            se = max(1, min(se, le - 1))

        confirm = int(getattr(self.cfg, "confirm_candles", 3))
        min_cfg = int(getattr(self.cfg, "min_candles", max(60, le)))
        mt = max(le + confirm, min_cfg)

        return se, le, mt

    def _prime_increments(self):
        for pid in self.coin_ids:
            try:
                # Coinbase Advanced Trade: get_product(product_id=...)
                prod = self.rest.get_product(product_id=pid)
                body = getattr(prod, "to_dict", lambda: {})() or {}
                # Prefer canonical AT fields; keep older fallbacks for safety
                price_inc = body.get("quote_increment") or body.get("price_increment") or "0.01"
                base_inc  = body.get("base_increment")  or body.get("base_min_size")   or "0.00000001"
                self.price_inc[pid] = float(price_inc)
                self.base_inc[pid]  = float(base_inc)
                min_mkt = (
                    body.get("min_market_base_size")
                    or body.get("min_market_order_size")
                    or body.get("base_min_market_size")
                    or 0.0
                )
                self.min_market_base_size[pid] = float(min_mkt or 0.0)
                #logging.info("Increments for %s: price_inc=%g, base_inc=%g, min_market_base_size=%g",pid, self.price_inc[pid], self.base_inc[pid], self.min_market_base_size[pid])
            except Exception:
                self.price_inc[pid] = 0.01
                self.base_inc[pid] = 1e-8
                self.min_market_base_size[pid] = 0.0
                logging.warning("Increments fallback in use for %s (check get_product).", pid)
                
    # -------------------- persistence --------------------
    def _load_portfolio(self):
        data = load_json(PORTFOLIO_FILE, {"positions": {}, "cost_basis": {}, "realized_pnl": 0.0, "entry_time": {}})
        for k, v in data.get("positions", {}).items():
            self.positions[k] = float(v)
        for k, v in data.get("cost_basis", {}).items():
            self.cost_basis[k] = float(v)
        self.realized_pnl = float(data.get("realized_pnl", 0.0))
        try:
            for pid, iso in (data.get("entry_time") or {}).items():
                if iso:
                    ts = datetime.fromisoformat(iso.replace("Z","+00:00")).timestamp()
                    self._entry_time[pid] = float(ts)
        except Exception:
            pass

    def _save_portfolio(self):
        et = {}
        for pid, ts in self._entry_time.items():
            if ts:
                try:
                    et[pid] = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat().replace("+00:00","Z")
                except Exception:
                    et[pid] = None
        save_json(PORTFOLIO_FILE, {
            "positions": self.positions,
            "cost_basis": self.cost_basis,
            "realized_pnl": float(self.realized_pnl),
            "entry_time": et
        })
    def _swab_log(self, detail: str) -> None:
        logging.info("\nYO SWAB!\n%s", detail)
        
    # -------------------- lifecycle --------------------
    def set_run_baseline(self):
        self.run_pnl_baseline = self.realized_pnl
        base_str = f"{self.run_pnl_baseline:.{PNL_DECIMALS}f}"
        logging.info("P&L baseline set for this run: $%s", base_str)

    def _backfill_seed_indicators(self):
        lookback = int(getattr(self.cfg, "warmup_candles", 200))
        enum = _granularity_enum(self.granularity_sec)
        gsec = int(self.granularity_sec)
        # strictly-past, bucket-aligned window; Coinbase caps around 300 candles
        now = int(time.time())
        end_ts = _align_to_bucket(now - 1, gsec)                 # never future
        want = min(max(lookback, 1), 300)
        start_ts = end_ts - (want + 5) * gsec                    # a few extra just in case

        for pid in self.coin_ids:
            try:
                # Advanced Trade requires UNIX second timestamps + enum granularity
                p = dict(
                    product_id=pid,
                    start=int(start_ts),   # send as UNIX seconds (int)
                    end=int(end_ts),       
                    granularity=enum,
                )
                logging.debug("Backfill params(unix/enum): %s", p)
                resp = self.rest.get_candles(**p)

                body = getattr(resp, "to_dict", lambda: resp)() or {}
                arr = list(body.get("candles", []) or [])
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
                    self._last_candle_close_ts[pid] = time.time()
                logging.info("Backfilled %s with %d candles (%s)", pid, len(arr), enum)
            except Exception as e:
                logging.debug("Backfill failed for %s: %s", pid, e)
                
    # ----------------------------------------------------------------
    # REST backstop: poll /candles while WS is idle to keep candles
    # progressing in LOCAL mode (or when WS candles stall).
    # ----------------------------------------------------------------
    def _rest_backstop_tick(self):
        """
        Poll a tiny candle window via REST when WS has been idle for a while,
        and advance _on_candle_close() so strategies keep running.
        Throttled by rest_backstop_period_s.
        """
        try:
            if not bool(getattr(self.cfg, "enable_rest_backstop", True)):
                return
            now = time.time()
            idle_for = now - float(self._last_msg_ts or 0.0)
            idle_thr = int(getattr(self.cfg, "rest_backstop_idle_s", 75))
            if idle_for < idle_thr:
                # Detect candle staleness even if WS is chatty (heartbeats/ticker)
                stall_s = int(self._stall_candle_factor) * int(self.granularity_sec)
                any_stale = False
                try:
                    for pid, last_ts in self._last_candle_close_ts.items():
                        if last_ts and (now - float(last_ts)) >= stall_s:
                            any_stale = True
                            break
                except Exception:
                    any_stale = False
                if idle_for < idle_thr and not any_stale:
                    return  # WS is fine; don’t poll

            # throttle REST polls
            period = int(getattr(self.cfg, "rest_backstop_period_s", 20))
            if now - float(self._last_rest_backstop_poll_ts or 0.0) < max(5, period):
                return
            self._last_rest_backstop_poll_ts = now

            enum = _granularity_enum(self.granularity_sec)
            gsec = int(self.granularity_sec)
            warm = int(getattr(self.cfg, "rest_backstop_warmup", 2))
            # strictly-past, bucket-aligned tiny window (fetch a few last-closed buckets)
            end_ts = _align_to_bucket(int(now) - 1, gsec)          # last CLOSED bucket start
            start_ts = end_ts - (max(1, warm) + 3) * gsec          # small window
            if start_ts >= end_ts:
                start_ts = end_ts - 2 * gsec

            for pid in self.coin_ids:
                try:
                    # Use UNIX seconds + enum granularity (no numeric fallback)
                    p = dict(
                        product_id=pid,
                        start=int(start_ts),   # UNIX seconds (int)
                        end=int(end_ts),
                        granularity=enum,
                    )
                    logging.debug("Backstop params(unix/enum): %s", p)
                    resp = self.rest.get_candles(**p)

                    body = getattr(resp, "to_dict", lambda: resp)() or {}
                    arr = list(body.get("candles", []) or [])
                    if not arr:
                        continue
                    arr.sort(key=lambda c: int(c.get("start", 0)))

                    last_seen = int(self._last_candle_start.get(pid) or 0)
                    # Walk forward: on any change in start, close previous with last known close.
                    for c in arr:
                        c_start = int(c.get("start"))
                        c_close = float(c.get("close"))
                        if last_seen and c_start > last_seen:
                            prev_close = float(self._cur_candle_close.get(pid) or c_close)
                            try:
                                self._on_candle_close(pid, last_seen, prev_close)
                            except Exception as _ce:
                                logging.debug("REST backstop close err %s: %s", pid, _ce)
                        self._last_candle_start[pid] = c_start
                        self._cur_candle_close[pid] = c_close
                        last_seen = c_start
                    # Nudge candle-stall watchdog
                    self._last_candle_close_ts[pid] = time.time()
                except Exception as _e:
                    try:
                        logging.error("REST backstop 400/err for %s with params start=%s end=%s gran=%s : %s",
                                      pid, start_ts, end_ts, enum, _e)
                    except Exception:
                        pass
        except Exception as e:
            logging.debug("REST backstop tick error: %s", e)

    def _subscribe_all(self):
        """Subscribe ticker always; optionally WS candles; best-effort heartbeats."""
        with self._ws_lock:
            # Always keep ticker for quotes/maker logic
            try:
                self.ws.ticker(product_ids=self.coin_ids)
            except TypeError:
                self.ws.ticker(self.coin_ids)
            # Candles if requested via WS; otherwise we will locally aggregate from ticker
            if self.candle_mode == "ws":
                try:
                    enum = _granularity_enum(self.granularity_sec)
                    # Try (kwargs), then (positional with kwargs), then (positional only)
                    try:
                        self.ws.candles(product_ids=self.coin_ids, granularity=enum)
                    except TypeError:
                        try:
                            self.ws.candles(self.coin_ids, granularity=enum)
                        except TypeError:
                            self.ws.candles(self.coin_ids)
                    logging.info("Subscribed to WS candles (%ds) and ticker for %s",
                                 self.granularity_sec, ", ".join(self.coin_ids))
                except Exception:
                    logging.info("WS candles unavailable; falling back to LOCAL aggregation.")
                    self.candle_mode = "local"
                    # Give builders time to produce the first local close
                    self._set_local_mode_grace(buckets=1.2)
            try:
                self.ws.heartbeats()
            except Exception:
                logging.debug("Heartbeats channel not available; continuing without it.")
            logging.info("Websocket subscriptions ready.")
            self._last_subscribe_ts = time.time()

    def open(self):
        self.ws.open()
        self._subscribe_all()
        # Start watchdog so we recover even if the SDK loop blocks.
        try:
            t = threading.Thread(target=self._watchdog_loop, name="ws-watchdog", daemon=True)
            t.start()
            logging.info("Watchdog thread started.")
        except Exception as e:
            logging.debug("Failed to start watchdog thread: %s", e)
        
    def _ws_ping_best_effort(self):
        try:
            fn = getattr(self.ws, "ping", None)
            if callable(fn):
                fn()
        except Exception:
            pass

    def _force_reconnect(self, reason: str):
        # Count *all* forced reconnects so flip-to-local can key off them if desired
        try:
            self._reconnect_tries += 1
            if isinstance(reason, str) and reason.lower().startswith("idle"):
                self._idle_reconnects += 1
        except Exception:
            pass
        with self._ws_lock:
            try:
                self.ws.close()
            except Exception:
                pass
            time.sleep(1.0)
            try:
                self.ws.open()
                self._subscribe_all()
                self._last_msg_ts = time.time()
                self._last_ping_ts = 0.0          # allow immediate ping
                self._last_subscribe_ts = time.time()  # belt-and-suspenders
                logging.warning("WS reconnected (%s).", reason)
            except Exception as e:
                logging.error("WS reconnect failed (%s): %s", reason, e)

    def _maybe_warn_and_recover_idle(self, now_ts: float | None = None):
        if now_ts is None:
            now_ts = time.time()
        idle = now_ts - float(self._last_msg_ts or 0.0)
        warn_s = int(getattr(self.cfg, "ws_idle_warn_s", 45))
        rc_s  = int(getattr(self.cfg, "ws_idle_reconnect_s", 120))
        if warn_s <= idle < rc_s:
            # throttle WARNs to once per minute (and once per minute-mark)
            last_mark = getattr(self, "_last_idle_warn_mark", None)
            this_mark = int(idle) // 60
            if this_mark != last_mark:
                logging.warning("WS idle for %ds (no messages).", int(idle))
                self._last_idle_warn_mark = this_mark
        if idle >= rc_s:
            self._force_reconnect(f"idle {int(idle)}s")
            # optional: repeated trouble → flip to local candles
            flip_after = int(getattr(self.cfg, "ws_idle_flip_to_local_after", 0))
            if flip_after > 0 and self._reconnect_tries >= flip_after:
                current_mode = self.candle_mode
                if current_mode != "local":
                    logging.warning(
                        "WS unstable (%d reconnects). Switching candle mode to LOCAL aggregation.",
                        self._reconnect_tries
                    )
                    self.candle_mode = "local"
                    self._set_local_mode_grace(buckets=1.2)

    def run_ws_forever(self):
        """
        Run the SDK loop with housekeeping: ping, periodic resubscribe, idle watchdog.
        """
        base_sleep = int(getattr(self.cfg, "ws_reconnect_backoff_base", 5))
        max_tries  = int(getattr(self.cfg, "ws_reconnect_max_tries", 999999))
        tries = 0
        while not self.stop_requested and tries < max_tries:
            fn = getattr(self.ws, "run_forever_with_exception_check", None)
            try:
                if callable(fn):
                    import inspect
                    # prefer a short-yielding loop if supported
                    if "sleep_seconds" in inspect.signature(fn).parameters:
                        fn(sleep_seconds=1.0)
                    else:
                        fn()
                else:
                    # fall back to run_forever() briefly, then regain control for housekeeping
                    run = getattr(self.ws, "run_forever", None)
                    if callable(run):
                        try:
                            run(timeout=2.0)
                        except TypeError:
                            run()
                    else:
                        raise RuntimeError("WS client has no run loop")

                # ---- HOUSEKEEPING TICK ----
                now = time.time()
                # best-effort ping
                ping_every = int(getattr(self.cfg, "ws_ping_interval_s", 30))
                if ping_every > 0 and (now - getattr(self, "_last_ping_ts", 0.0)) >= ping_every:
                    self._ws_ping_best_effort()
                    self._last_ping_ts = now
                # periodic resubscribe
                resub_every = int(getattr(self.cfg, "ws_resubscribe_interval_s", 900))
                if resub_every > 0 and (now - getattr(self, "_last_subscribe_ts", 0.0)) >= resub_every:
                    logging.info("Reissuing WS subscriptions (periodic resubscribe).")
                    self._subscribe_all()
                # idle watchdog (warn/reconnect/optional flip)
                self._maybe_warn_and_recover_idle(now)
                
                # REST backstop while WS is dark
                self._rest_backstop_tick()
                
                # --- Candle-stall watchdog (candles not progressing even if WS is "alive") ---
                try:
                    stall_s = int(self._stall_candle_factor) * int(self.granularity_sec)
                    stalled = []
                    for pid, last_ts in self._last_candle_close_ts.items():
                        if now < float(self._stall_grace_until.get(pid, 0.0)):
                            continue
                        if last_ts and (now - float(last_ts)) >= stall_s:
                            stalled.append(pid)
                     
                    if stalled:
                        # If the majority of coins are stalled, prefer local aggregation immediately.
                        try:
                            pct = len(stalled) / max(1, len(self.coin_ids))
                        except Exception:
                            pct = 0.0
                        majority_thresh = float(getattr(self.cfg, "stall_majority_flip_threshold", 0.6))
                        if self.candle_mode == "ws" and pct >= majority_thresh:
                            logging.warning(
                                "Majority of coins stalled (%.0f%%). Switching candle mode to LOCAL aggregation.",
                                pct * 100.0
                            )
                            self.candle_mode = "local"
                            self._set_local_mode_grace(buckets=1.2)

                        # Debounce the action to avoid spamming resubscribe/reconnect.
                        cooldown = int(getattr(self.cfg, "stall_action_cooldown_s", 30))
                        if now - float(getattr(self, "_last_stall_action_ts", 0.0)) < max(5, cooldown):
                            # Already acted recently; only track a new stall window.
                            self._consecutive_stall_windows += 1
                        else:
                            logging.warning(
                                "Candle stall detected for %s (>%ds without a close). Forcing resubscribe.",
                                ", ".join(sorted(set(stalled))), stall_s
                            )
                            self._subscribe_all()
                            self._last_stall_action_ts = now
                            self._consecutive_stall_windows += 1

                            # Escalate to hard reconnect after N debounced stall windows
                            esc_after = int(getattr(self.cfg, "stall_hard_reconnect_after", 2))
                            if self._consecutive_stall_windows >= max(1, esc_after):
                                self._force_reconnect("candle stall")
                                self._consecutive_stall_windows = 0
                                self._stall_total += 1
                                flip_after = int(getattr(self.cfg, "stall_flip_to_local_after", 0))
                                if flip_after > 0 and self.candle_mode != "local" and self._stall_total >= flip_after:
                                    logging.warning("Repeated candle stalls. Switching candle mode to LOCAL aggregation.")
                                    self.candle_mode = "local"
                    else:
                        # Healthy progress resets the consecutive stall counters
                        self._consecutive_stall_windows = 0
                except Exception as _stall_e:
                    logging.debug("Stall watchdog check failed: %s", _stall_e)
                
                # healthy loop → reset backoff
                tries = 0
                # ---------- Low-noise telemetry heartbeat ----------
                # One compact line every telemetry_heartbeat_s seconds (default: 1800s = 30min)
                try:
                    hb_every = int(getattr(self.cfg, "telemetry_heartbeat_s", 1800))
                except Exception:
                    hb_every = 1800
                last_hb = float(getattr(self, "_last_heartbeat_ts", 0.0) or 0.0)
                if hb_every > 0 and (now - last_hb) >= hb_every:
                    since = int(now - float(self._last_msg_ts or 0.0))
                    cc = int(getattr(self, "_candles_closed", 0) or 0)
                    logging.info(
                        "HB: last WS msg %ds ago | candles_closed+%d | mode=%s | idle_reconnects=%d | reconnect_tries=%d",
                        since, cc, self.candle_mode.upper(),
                        int(getattr(self, "_idle_reconnects", 0)),
                        int(getattr(self, "_reconnect_tries", 0)),
                    )
                    self._candles_closed = 0
                    self._last_heartbeat_ts = now
                # ---------------------------------------------------
            except Exception as e:
                tries += 1
                self._reconnect_tries = tries
                sleep_s = min(self._ws_backoff_max, max(1, base_sleep * (2 ** min(tries - 1, 5))))
                logging.error("Websocket loop error/exit (%s). Reconnecting in %ds (try %d)…", e, sleep_s, tries)
                time.sleep(sleep_s)
                # hard reconnect sequence (close → open → resubscribe)
                self._force_reconnect("loop error/exit")


    def close(self):
        if not self.session_footer_written:
            self.log_session_pnl()
            self.session_footer_written = True
        try:
            self.ws.close()
        except Exception:
            pass

    # -------------------- kpi csv --------------------
    def _append_trade_csv(self, *, ts_iso: str, order_id: str | None, side: str, coin_id: str,
                          size: float, price: float, fee: float | None,
                          liquidity: str | None, pnl: float | None,
                          position_after: float | None, cost_basis_after: float | None,
                          intent_price: float | None, hold_time_sec: float | None,
                          entry_reason: str | None = None, exit_reason: str | None = None):
        try:
            from .constants import TRADES_CSV_FILE
        except Exception:
            return
        headers = [
            "ts","order_id","side","coin","size","price","quote_usd","fee","liquidity",
            "pnl","position_after","cost_basis_after","intent_price","slippage_abs","slippage_bps",
            "hold_time_sec","entry_reason","exit_reason"
        ]
        quote_usd = (size * price) if (size is not None and price is not None) else None
        # If an order was reconciled after a restart (or via cancel/repost) the in-memory
        # intent might be missing. Since BUYs only come from the EMA path right now,
        # default the reason to "ema_cross" so CSV is always informative.
        try:
            s_up = (side or "").upper()
            if s_up == "BUY" and not entry_reason:
                logging.debug("CSV: missing entry_reason for BUY %s (order_id=%s); defaulting to ema_cross.",
                              coin_id, order_id)
                entry_reason = "ema_cross"
            elif s_up == "SELL" and not exit_reason:
                # Keep blank (various SELL reasons) but note for debugging
                logging.debug("CSV: missing exit_reason for SELL %s (order_id=%s).", coin_id, order_id)
        except Exception:
            pass
        
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
        row = [ts_iso, order_id or "", side, coin_id, f"{size:.10f}" if size is not None else "",
               f"{price:.8f}" if price is not None else "", f"{quote_usd:.2f}" if quote_usd is not None else "",
               f"{fee:.6f}" if fee is not None else "", liquidity or "",
               f"{pnl:.8f}" if pnl is not None else "",
               f"{position_after:.10f}" if position_after is not None else "",
               f"{cost_basis_after:.8f}" if cost_basis_after is not None else "",
               f"{intent_price:.8f}" if intent_price is not None else "",
               f"{slippage_abs:.8f}" if slippage_abs is not None else "",
               f"{slippage_bps:.4f}" if slippage_bps is not None else "",
               f"{hold_time_sec:.2f}" if hold_time_sec is not None else "",
               (entry_reason or ""), (exit_reason or "")]
        try:
            path = TRADES_CSV_FILE
            path.parent.mkdir(parents=True, exist_ok=True)
            new_file = not path.exists()
            # single-writer guard (prevents interleaved lines)
            with self._state_lock:
                with open(path, "a", newline="", encoding="utf-8") as f:
                    w = csv.writer(f, lineterminator="\n")
                    if new_file:
                        w.writerow(headers)
                    w.writerow(row)
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
            # Flush any previously queued local candles first
            self._flush_local_settle(now_ts=ts_now)
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
                        # If this is the first tick starting a fresh local candle, seed a per-coin grace
                        if self._builders[pid].start is not None:
                            # Only seed when grace is not already active
                            if time.time() >= float(self._stall_grace_until.get(pid, 0.0)):
                                self._stall_grace_until[pid] = time.time() + max(1.0, 0.25 * self.granularity_sec)
                                logging.debug("Local mode grace seeded for %s for ~%.0fs.", pid, 0.25 * self.granularity_sec)
                        if closed is not None:
                            start, o, h, l, c, v = closed
                            logging.debug("LOCAL CANDLE %s @ %ds close=%.8f", pid, self.granularity_sec, c)
                            if self._local_settle_ms > 0:
                                release = ts_now + (self._local_settle_ms / 1000.0)
                                self._local_settle_q.append((release, pid, start, c))
                            else:
                                self._on_candle_close(pid, start, c)
            # After processing this WS message, try another flush
            self._flush_local_settle(now_ts=ts_now)

        elif ch == "candles":
            # If we flipped to LOCAL, ignore WS candle frames to avoid double-processing.
            if self.candle_mode != "ws":
                return
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
                        logging.debug("WS: Received candle for %s at %s", pid, c.get("start"))
                        self._on_candle_close(pid, last_start, self._cur_candle_close.get(pid, close))
                        self._last_candle_start[pid] = start
                    self._cur_candle_close[pid] = close
                    
    # Add alongside other methods (e.g., just above run_ws_forever)
    def _watchdog_loop(self):
        """Out-of-band watchdog that runs even if the SDK WS loop blocks."""
        step = 1.0
        while not getattr(self, "stop_requested", False):
            try:
                now = time.time()
                # idle watchdog (warn/reconnect/optional flip)
                self._maybe_warn_and_recover_idle(now)
                
                # REST backstop while WS is dark
                self._rest_backstop_tick()

                # candle-stall watchdog (debounced; mirrors run_ws_forever)
                try:
                    stall_s = int(self._stall_candle_factor) * int(self.granularity_sec)
                    stalled = []
                    for pid, last_ts in self._last_candle_close_ts.items():
                        # Skip if coin is in grace (e.g., right after switching to LOCAL)
                        if now < float(self._stall_grace_until.get(pid, 0.0)):
                            continue
                        if last_ts and (now - float(last_ts)) >= stall_s:
                            stalled.append(pid)
                    if stalled:
                        try:
                            pct = len(stalled) / max(1, len(self.coin_ids))
                        except Exception:
                            pct = 0.0
                        majority_thresh = float(getattr(self.cfg, "stall_majority_flip_threshold", 0.6))
                        if self.candle_mode == "ws" and pct >= majority_thresh:
                            logging.warning(
                                "Majority of coins stalled (%.0f%%). Switching candle mode to LOCAL aggregation.",
                                pct * 100.0
                            )
                            self.candle_mode = "local"
                            self._set_local_mode_grace(buckets=1.2)

                        cooldown = int(getattr(self.cfg, "stall_action_cooldown_s", 30))
                        if now - float(getattr(self, "_last_stall_action_ts", 0.0)) < max(5, cooldown):
                            self._consecutive_stall_windows += 1
                        else:
                            logging.warning(
                                "Candle stall detected for %s (>%ds without a close). Forcing resubscribe.",
                                ", ".join(sorted(set(stalled))), stall_s
                            )
                            self._subscribe_all()
                            self._last_stall_action_ts = now
                            self._consecutive_stall_windows += 1

                            esc_after = int(getattr(self.cfg, "stall_hard_reconnect_after", 2))
                            if self._consecutive_stall_windows >= max(1, esc_after):
                                self._force_reconnect("candle stall (watchdog)")
                                self._consecutive_stall_windows = 0
                                self._stall_total += 1
                                flip_after = int(getattr(self.cfg, "stall_flip_to_local_after", 0))
                                if flip_after > 0 and self.candle_mode != "local" and self._stall_total >= flip_after:
                                    logging.warning("Repeated candle stalls. Switching candle mode to LOCAL aggregation.")
                                    self.candle_mode = "local"
                    else:
                        self._consecutive_stall_windows = 0
                except Exception as _stall_e:
                    logging.debug("Watchdog stall check failed: %s", _stall_e)

                # best-effort ping + periodic resubscribe (keeps state fresh even if WS blocked)
                try:
                    ping_every = int(getattr(self.cfg, "ws_ping_interval_s", 30))
                    if ping_every > 0 and (now - getattr(self, "_last_ping_ts", 0.0)) >= ping_every:
                        self._ws_ping_best_effort()
                        self._last_ping_ts = now
                    resub_every = int(getattr(self.cfg, "ws_resubscribe_interval_s", 900))
                    if resub_every > 0 and (now - getattr(self, "_last_subscribe_ts", 0.0)) >= resub_every:
                        logging.info("Reissuing WS subscriptions (watchdog resubscribe).")
                        self._subscribe_all()
                except Exception as _hb_e:
                    logging.debug("Watchdog hb/resubscribe failed: %s", _hb_e)

            except Exception as e:
                logging.debug("Watchdog loop error: %s", e)
            time.sleep(step)


    # -------------------- signal & orders --------------------
    def evaluate_signal(self, coin_id: str, price: float, s: float, l: float):
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
        if coin_id not in self._primed:
            self._trend[coin_id] = rel
            self._primed.add(coin_id)
            return

        prev = self._trend[coin_id]

        # Confirmation logic
        if rel == 0:
            # LOCAL-ONLY: allow one neutral (band) candle to preserve confirmations
            st = self._pending.get(coin_id, {"rel": 0, "count": 0, "band_grace": 0})
            local_mode = (self.candle_mode == "local")
            if local_mode and st.get("rel", 0) != 0 and int(st.get("band_grace", 0)) == 0:
                # First neutral after a directional run → keep count, mark grace used
                st["band_grace"] = 1
                self._pending[coin_id] = st
            else:
                # Multiple neutrals or not in a run → reset as before
                self._pending[coin_id] = {"rel": 0, "count": 0, "band_grace": 0}
            return

        st = self._pending.get(coin_id, {"rel": 0, "count": 0, "band_grace": 0})
        if not st or st["rel"] != rel:
            # new direction or first time seeing this coin this session
            st = {"rel": rel, "count": 1, "band_grace": 0}
        else:
            st["count"] += 1
            st["band_grace"] = 0  # any directional candle clears the grace

        # optional cap to keep count bounded in long trends
        st["count"] = min(st["count"], 32)
        self._pending[coin_id] = st

        # dynamic confirms — read live cfg so mid-run AutoTune applies
        need = max(1, int(getattr(self.cfg, "confirm_candles",
                          getattr(self.cfg, "confirm_ticks", 2))))

        if st["count"] < need:
            return

        # confirmed cross; reset pending
        self._pending[coin_id] = {"rel": 0, "count": 0, "band_grace": 0}
        if rel == prev:
            return
        self._trend[coin_id] = rel
        signal = rel  # +1 buy, -1 sell

        cooldown_s = int(getattr(self.cfg, "per_coin_cooldown_s", getattr(self.cfg, "cooldown_sec", 300)))
        if not self.last.ok(coin_id, cooldown_s):
            return

        # --- v1.0.3: Just-in-time reconcile before SELL position check ---
        if signal < 0 and getattr(self.cfg, "reconcile_on_sell_attempt", False):
            try:
                self.reconcile_now(hours=getattr(self.cfg, "lookback_hours", 48))
            except Exception as e:
                logging.debug("Pre-SELL reconcile failed for %s: %s", coin_id, e)

        # SELL guardrails: must hold position
        if signal < 0:
            try:
                live_avail = float(self._get_live_available_base(coin_id))
            except Exception:
                live_avail = 0.0
            if max(float(self.positions.get(coin_id, 0.0)), live_avail) <= 0.0:
                logging.info("Skip SELL %s: no position held (cache=%.10f live=%.10f).",
                     coin_id, float(self.positions.get(coin_id, 0.0)), live_avail)
                return


        # Optional hard stop: if enabled and below CB by X bps, force market exit now
        if signal < 0:
            hs = getattr(self.cfg, "hard_stop_bps", None)
            if hs is not None:
                cb = float(self.cost_basis.get(coin_id, 0.0) or 0.0)
                if cb > 0.0:
                    floor = cb * (1.0 - float(hs) / 10_000.0)
                    if price <= floor:
                        # Always cross-check with live-available before SELL sizing
                        held_cache = max(0.0, float(self.positions.get(coin_id, 0.0)))
                        try:
                            live_avail = float(self._get_live_available_base(coin_id))
                        except Exception:
                            live_avail = -1.0
                        held = held_cache
                        if live_avail >= 0.0:
                            held = min(held_cache, live_avail)
                            # If cache was empty but live shows balance, seed cache for later logic
                            if held_cache <= 0.0 and live_avail > 0.0:
                                self.positions[coin_id] = live_avail
                        
                        if held > 0.0 and price > 0.0:
                            quote_usd = held * price
                            logging.info(
                                "[HARD STOP TRIGGER] Will attempt MARKET SELL %s after availability checks: "
                                "last=%.8f floor=%.8f held=%.10f quote~$%.2f",
                                coin_id, price, floor, held, quote_usd
                            )
                            # Route through place_order so 'reason' is captured in _intent → trades.csv
                            self.place_order(
                                coin_id, side="SELL", quote_usd=quote_usd, last_price=price, reason="stop_loss"
                            )
                        return

        # Advisors (optional): veto only if clearly bad
        if self.enable_advisors:
            rsi_val = self._rsi[coin_id].value
            macd_hist = self._macd[coin_id].hist
            if not advisor_allows("BUY" if signal > 0 else "SELL", rsi_val, macd_hist, self.advisor_settings, price):
                logging.info(
                    "Advisor veto %s %s (RSI=%s, MACD_hist=%s)",
                    "BUY" if signal > 0 else "SELL",
                    coin_id,
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
                logging.info("\n\n**********Daily BUY cap reached (%.2f). Skipping further BUYs.**********\n", daily_cap)
                self.log_session_pnl()
                self.daily_cap_reached_logged = True
                self.session_footer_written = True
            return

        notional = float(getattr(self.cfg, "usd_per_order", 1.0))
        if side == "BUY":
            notional = min(notional, remaining)

        try:
            # EMA captain path — tag reason explicitly
            self.place_order(coin_id, side=side, quote_usd=notional, last_price=price, reason="ema_cross")
        except Exception as e:
            logging.exception("Order error for %s: %s", coin_id, e)

    def _submit_limit_maker_order(self, coin_id: str, side: str, base_size: float, limit_price: float):
        client_order_id = f"ema-{coin_id}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        p_dec = decimals_from_inc(self.price_inc.get(coin_id, 0.01))
        b_dec = decimals_from_inc(self.base_inc.get(coin_id, 1e-8))
        limit_price_str = f"{limit_price:.{p_dec}f}"
        base_size_str = f"{base_size:.{b_dec}f}"

        params = {
            "client_order_id": client_order_id,
            "product_id": coin_id,
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
            return self._resp_ok(resp), resp
        except Exception as e:
            return False, e

    def _submit_market_order(self, coin_id: str, side: str, quote_usd: float):
        client_order_id = f"ema-{coin_id}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        params = {"client_order_id": client_order_id, "product_id": coin_id}
        if self.portfolio_id:
            params["portfolio_id"] = self.portfolio_id
        try:
            if side == "BUY":
                params["quote_size"] = f"{quote_usd:.2f}"
                resp = self.rest.market_order_buy(**params)
            else:
                # SELL: use base_size and clamp to live-available position
                held = max(0.0, float(self.positions.get(coin_id, 0.0)))
                live_avail = self._get_live_available_base(coin_id)
                held = min(held, live_avail)

                try:
                    min_base = float(self.base_inc.get(coin_id, 1e-8))
                except Exception:
                    min_base = 1e-8
                if held < (min_base * 0.99):
                    logging.info(
                        "Skip SELL %s: no live-available base (held=%.10f, avail=%.10f, inc=%g).",
                        coin_id,
                        float(self.positions.get(coin_id, 0.0)),
                        float(live_avail),
                        float(self.base_inc.get(coin_id, 1e-8)),
                    )
                    return False, ValueError("No live-available base to sell")

                last = float(self.quotes.get(coin_id, {}).get("last") or 0.0)
                intended_base = quote_usd / last if last > 0 else held
                base_size = min(held, intended_base)
                try:
                    base_inc = float(self.base_inc.get(coin_id, 1e-8))
                except Exception:
                    base_inc = 1e-8
                
                # --- FLOAT-SAFE EPSILON CLAMP (pre-round) ---
                # If we’re within half an increment of the held amount, snap to held
                if abs(base_size - held) <= (0.5 * base_inc):
                    base_size = held
                # Nudge by a tiny epsilon relative to increment so 1*inc doesn't fall just below inc
                EPS = base_inc * 1e-6
                base_size = max(0.0, base_size + EPS)
                shave_steps = int(getattr(self.cfg, "full_exit_shave_increments", 1))
                
                shaved = False
                if (
                    shave_steps > 0 and held >= (shave_steps + 1) * base_inc and base_size > 0
                    and abs(base_size - held) <= (2 * base_inc + 1e-15)
                ):
                    before = base_size
                    self._swab_log(
                        f"Applying full-exit shave for {coin_id}: intended={before:.10f}, "
                        f"held={held:.10f}, inc={base_inc}, steps={shave_steps}…"
                    )
                    base_size = max(base_inc, held - shave_steps * base_inc)
                    shaved = True
                    
                # Snap to increment & zero-size guard
                inc_for_log = base_inc
                base_size = round_down_to_inc(max(0.0, base_size), inc_for_log)
                if base_size < inc_for_log - 1e-18:
                    logging.info(
                        "Skip SELL %s: base_size below increment %s(held=%.10f, inc=%s).",
                        coin_id,
                        "after shave " if shaved else "after rounding ",
                        float(held), str(inc_for_log),
                    )
                    # mark dust suppression to avoid repeated attempts
                    try:
                        self._qm_dust_suppress_until[coin_id] = time.time() + 30 * 60
                    except Exception:
                        pass
                    return False, ValueError("No base size to sell")
                    
                # Enforce min market base size if required
                min_mkt = float(self.min_market_base_size.get(coin_id, 0.0) or 0.0)
                if min_mkt > 0.0 and base_size < min_mkt:
                    if held >= min_mkt:
                        base_size = round_down_to_inc(min(held, max(base_size, min_mkt)), inc_for_log)
                    else:
                        logging.info(
                            "Skip SELL %s: below min_market_base_size (base_size=%.10f < %.10f, held=%.10f).",
                            coin_id, base_size, min_mkt, held
                        )
                        # mark dust suppression to avoid repeated attempts
                        try:
                            self._qm_dust_suppress_until[coin_id] = time.time() + 30 * 60
                        except Exception:
                            pass
                        return False, ValueError("Below min_market_base_size")

                dec = decimals_from_inc(inc_for_log)
                params["base_size"] = f"{base_size:.{dec}f}"
                resp = self.rest.market_order_sell(**params)
                
            # Normalize success based on response content
            ok = self._resp_ok(resp)
            # Special-case insufficient funds preview → mark not ok and throttle QM
            try:
                body = getattr(resp, "to_dict", lambda: resp)() or {}
                if body and (not body.get("success", True)):
                    err = (body.get("error_response") or {}).get("error") or ""
                    if "INSUFFICIENT" in str(err).upper():
                        self._qm_last_ts[coin_id] = time.time()
                        ok = False
            except Exception:
                pass
            return ok, resp

            
        except Exception as e:
            return False, e


    def place_order(self, coin_id: str, side: str, quote_usd: float, last_price: float, reason: str = "ema_cross"):
        logging.info("[INTENT] %s %s $%.2f (last=%.8f, reason=%s)", side.upper(), coin_id, float(quote_usd or 0.0), float(last_price or 0.0), reason)
        side = side.upper()
        assert side in {"BUY", "SELL"}

        display_qty = quote_usd / last_price if last_price > 0 else 0.0
        dry_run = bool(getattr(self.cfg, "dry_run", False))
        log_trade(coin_id, side, quote_usd, last_price, display_qty, dry_run)

        if dry_run:
            if side == "BUY":
                self.session_cash_pnl -= quote_usd
                self.spend.add(quote_usd)  # BUY-only
            else:
                self.session_cash_pnl += quote_usd
            self.last.stamp(coin_id)
            logging.info("[DRY RUN] %s %s $%.2f (reason=%s)", side, coin_id, quote_usd, reason)
            return

        # Side-aware maker preference
        prefer_maker = bool(getattr(self.cfg, "prefer_maker", True))
        if side == "SELL":
            prefer_maker = bool(getattr(self.cfg, "prefer_maker_for_sells", prefer_maker))

        # Quartermaster & hard-stop exits must be immediate: force MARKET SELL
        forced_market = (side == "SELL" and reason in ("take_profit", "stagnation", "stop_loss"))
        if forced_market:
            prefer_maker = False
            logging.info("[ATTEMPT] MARKET SELL for %s due to reason=%s", coin_id, reason)

        if prefer_maker:
            q = self.quotes.get(coin_id, {})
            limit_price, base_size = compute_maker_limit(
                coin_id=coin_id,
                side=side,
                last_price=last_price,
                price_inc=self.price_inc.get(coin_id, 0.01),
                base_inc=self.base_inc.get(coin_id, 1e-8),
                usd_per_order=float(getattr(self.cfg, "usd_per_order", 1.0)),
                offset_bps=getattr(self.cfg, "maker_offset_bps_per_coin", {}).get(
                    coin_id, float(getattr(self.cfg, "maker_offset_bps", 5.0))
                ),
                bid=q.get("bid"),
                ask=q.get("ask"),
            )
            if side == "SELL":
                # clamp maker SELL size to live available (avoids reserve/lock issues)
                held = max(0.0, float(self.positions.get(coin_id, 0.0)))
                try:
                    live_avail = float(self._get_live_available_base(coin_id))
                except Exception:
                    live_avail = held
                base_size = min(base_size, held, live_avail)
                try:
                    base_inc = float(self.base_inc.get(coin_id, 1e-8))
                except Exception:
                    base_inc = 1e-8
                min_mkt = float(self.min_market_base_size.get(coin_id, 0.0) or 0.0)
                # Round down to increment
                base_size = round_down_to_inc(max(0.0, base_size), base_inc)
                # Enforce min market base size if defined
                if min_mkt > 0.0 and base_size < min_mkt:
                    if held >= min_mkt:
                        base_size = round_down_to_inc(min(held, max(base_size, min_mkt)), base_inc)
                    else:
                        logging.info(
                            "Skip maker SELL %s: below min_market_base_size (base_size=%.10f < %.10f, held=%.10f).",
                            coin_id, base_size, min_mkt, held
                        )
                        # mark dust suppression to avoid repeated attempts for a short window
                        try:
                            self._qm_dust_suppress_until[coin_id] = time.time() + 30 * 60
                        except Exception:
                            pass
                        return

            if base_size <= 0 or limit_price <= 0:
                logging.error("Invalid maker params for %s %s: price=%.8f size=%.8f", side, coin_id, limit_price, base_size)
                return
            ok, resp = self._submit_limit_maker_order(coin_id, side, base_size, limit_price)
        else:
            ok, resp = self._submit_market_order(coin_id, side, quote_usd)

        if not ok:
            # If this was a forced market exit, be explicit that execution failed.
            if 'forced_market' in locals() and forced_market:
                logging.warning("[FAILED] MARKET SELL for %s (reason=%s) did not execute.", coin_id, reason)
            logging.error("%s order FAILED for %s $%.2f: %s", side, coin_id, quote_usd, resp)
            return

        # BUY-only daily spend
        if side == "BUY":
            self.spend.add(quote_usd)
        self.last.stamp(coin_id)

        # Success logging — only claim EXECUTED for forced-market sells after success
        try:
            body = getattr(resp, "to_dict", lambda: resp)()
            if side == "SELL":
                held_dbg = float(self.positions.get(coin_id, 0.0))
                try:
                    avail_dbg = float(self._get_live_available_base(coin_id))
                except Exception:
                    avail_dbg = -1.0
                base_inc_dbg = float(self.base_inc.get(coin_id, 1e-8))
                logging.info(
                    "Live SELL %s $%.2f placed. held=%.10f avail=%.10f base_inc=%g Resp: %s (reason=%s)",
                    coin_id, quote_usd, held_dbg, avail_dbg, base_inc_dbg, body, reason
                )
                if 'forced_market' in locals() and forced_market:
                    logging.info("[EXECUTED] MARKET SELL %s (reason=%s)", coin_id, reason)
            else:
                logging.info("Live BUY %s $%.2f placed. Resp: %s (reason=%s)", coin_id, quote_usd, body, reason)
        except Exception:
            if side == "SELL":
                logging.info("Live SELL %s $%.2f placed. (reason=%s)", coin_id, quote_usd, reason)
            else:
                logging.info("Live BUY %s $%.2f placed. (reason=%s)", coin_id, quote_usd, reason)
        
        # best-effort immediate fills -> update portfolio
        try:
            body = getattr(resp, "to_dict", lambda: resp)()
            order_id = (body.get("success_response", {}).get("order_id") or body.get("order_id"))

            # record intent snapshot for slippage/KPIs
            try:
                q = self.quotes.get(coin_id, {})
                self._intent[str(order_id)] = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "product_id": coin_id,
                    "side": side,
                    "intent_price": float(last_price) if last_price else None,
                    "bid": q.get("bid"),
                    "ask": q.get("ask"),
                    "reason": reason,  # propagate exit/entry reason to CSV later
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

                # Sort immediate fills oldest → newest to keep CSV chronological
                page = list(fb.get("fills", []) or [])
                page.sort(
                    key=lambda f: self._iso_to_dt(f.get("trade_time") or f.get("time"))
                                  or datetime.min.replace(tzinfo=timezone.utc)
                )

                # Guard per-fill mutations + CSV to avoid races with reconcile thread
                with self._state_lock:
                    for f in page:
                        fp = self._fill_fingerprint(f)
                        if self._processed_fills.has(fp):
                            continue
                        side_f = (f.get("side") or f.get("order_side") or "").upper()
                        pid_f = f.get("product_id")
                        size_f = float(f.get("size") or f.get("base_size") or f.get("filled_size") or 0.0)
                        price_f = float(f.get("price") or 0.0)
                        fee_f = float(f.get("fee") or 0.0)
                        # Snapshots (before mutation) for precise CSV on SELLs
                        qty_before_snapshot = float(self.positions.get(pid_f, 0.0))
                        cb_before_snapshot  = float(self.cost_basis.get(pid_f, 0.0))
                        if side_f == "BUY":
                            qty_before = self.positions[pid_f]
                            new_qty = qty_before + size_f
                            new_cost = self.cost_basis[pid_f] * self.positions[pid_f] + (size_f * price_f) + fee_f
                            if new_qty > 0:
                                self.positions[pid_f] = new_qty
                                self.cost_basis[pid_f] = new_cost / new_qty
                            # mark entry time when position transitions flat -> long
                            if qty_before <= 0.0 and new_qty > 0.0:
                                self._entry_time[pid_f] = time.time()
                        elif side_f == "SELL":
                            qty_before = self.positions[pid_f]
                            sell_qty = min(size_f, qty_before)
                            self.realized_pnl += sell_qty * (price_f - self.cost_basis[pid_f]) - fee_f
                            self.positions[pid_f] = max(0.0, qty_before - sell_qty)
                            if self.positions[pid_f] == 0.0:
                                self.cost_basis[pid_f] = 0.0
                        self._processed_fills.add(fp, {"t": f.get("trade_time") or f.get("time")})
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
                            reason_val = (intent.get("reason") or "")
                            entry_reason = reason_val if side_f == "BUY" else ""
                            exit_reason  = reason_val if side_f == "SELL" else ""
                            pnl_fill = None
                            if side_f == "SELL":
                                try:
                                    sell_qty_csv = min(size_f, qty_before_snapshot)
                                    pnl_fill = sell_qty_csv * (price_f - cb_before_snapshot) - fee_f
                                except Exception:
                                    pnl_fill = None
                            hold_time_sec = None
                            try:
                                if side_f == "SELL":
                                    if self.positions[pid_f] == 0.0 and self._entry_time.get(pid_f):
                                        hold_time_sec = max(0.0, time.time() - self._entry_time[pid_f])
                                        self._entry_time[pid_f] = None
                            except Exception:
                                pass
                            self._append_trade_csv(ts_iso=ts_iso, order_id=oid, side=side_f, coin_id=pid_f,
                                                   size=size_f, price=price_f, fee=fee_f,
                                                   liquidity=flag, pnl=pnl_fill,
                                                   position_after=self.positions.get(pid_f),
                                                   cost_basis_after=self.cost_basis.get(pid_f),
                                                   intent_price=intent_price, hold_time_sec=hold_time_sec,
                                                   entry_reason=entry_reason, exit_reason=exit_reason)
                        except Exception as e:
                            logging.debug("CSV log (immediate) failed: %s", e)

                if any_new:
                    # prune & save via helper
                    max_keys = int(getattr(self.cfg, "processed_fills_max", 10000))
                    # Swab shout (before cleanup)
                    try:
                        pre_entries = len(self._processed_fills.to_dict())
                    except Exception:
                        pre_entries = -1
                    self._swab_log(f"Pruning processed fills (max={max_keys}, current_entries={pre_entries})…")
                    # Now do cleanup
                    self._processed_fills.prune(max_keys=max_keys)
                    with self._state_lock:
                        save_json(PROCESSED_FILLS_FILE, self._processed_fills.to_dict())
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

    def _iso_to_dt(self, s: str | None):
        """Parse '...Z' or ISO string to aware datetime in UTC; return None on failure."""
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None

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
            "limit": 1000,
        }
        fills = []
        try:
            while True:
                resp = self.rest.get("/api/v3/brokerage/orders/historical/fills", params=params)
                fb = getattr(resp, "to_dict", lambda: resp)()
                page = fb.get("fills", []) or []
                fills.extend(page)
                cursor = fb.get("cursor") or fb.get("next_cursor") or fb.get("pagination", {}).get("cursor")
                if not cursor or not page:
                    break
                params["cursor"] = cursor
        except Exception as e:
            logging.debug("Could not fetch recent fills: %s", e)
            return

        # --- Enforce time window & chronological order (oldest → newest)
        start_dt = end - timedelta(hours=lookback_hours)
        end_dt = end

        def _in_window(f):
            dt = self._iso_to_dt(f.get("trade_time") or f.get("time"))
            return (dt is not None) and (start_dt <= dt <= end_dt)

        before_ct = len(fills)
        fills = [f for f in fills if _in_window(f)]
        dropped = before_ct - len(fills)

        fills.sort(
            key=lambda f: self._iso_to_dt(f.get("trade_time") or f.get("time"))
                          or datetime.min.replace(tzinfo=timezone.utc)
        )

        if dropped > 0:
            logging.info(
                "Reconcile: dropped %d out-of-range fills (kept %d within %dh window).",
                dropped, len(fills), lookback_hours
            )

        changed = False
        for f in fills:
            fp = self._fill_fingerprint(f)
            if self._processed_fills.has(fp):
                continue

            side = (f.get("side") or f.get("order_side") or "").upper()
            pid = f.get("product_id")
            if not pid or side not in {"BUY", "SELL"}:
                self._processed_fills.add(fp, {"skip": True})
                changed = True
                continue

            try:
                size = float(f.get("size") or f.get("base_size") or f.get("filled_size") or 0.0)
                price = float(f.get("price") or 0.0)
                fee = float(f.get("fee") or 0.0)
            except Exception:
                self._processed_fills.add(fp, {"bad_num": True})
                changed = True
                continue

            # Guard each fill’s state mutations + CSV with the lock
            with self._state_lock:
                # --- SNAPSHOT before any mutation (for correct SELL P&L/CSV) ---
                qty_before_snapshot = float(self.positions.get(pid, 0.0))
                cb_before_snapshot  = float(self.cost_basis.get(pid, 0.0))
                if side == "BUY":
                    qty_before = qty_before_snapshot
                    new_qty = qty_before + size
                    new_cost = self.cost_basis[pid] * qty_before + (size * price) + fee
                    if new_qty > 0:
                        self.positions[pid] = new_qty
                        self.cost_basis[pid] = new_cost / new_qty
                    if qty_before <= 0.0 and new_qty > 0.0:
                        self._entry_time[pid] = time.time()
                else:
                    qty_before = qty_before_snapshot
                    sell_qty = min(size, qty_before)
                    pnl_add = sell_qty * (price - self.cost_basis[pid]) - fee
                    self.realized_pnl += pnl_add
                    self.positions[pid] = max(0.0, qty_before - sell_qty)
                    if self.positions[pid] == 0.0:
                        self.cost_basis[pid] = 0.0

                self._processed_fills.add(fp, {"t": f.get("trade_time") or f.get("time")})

                # KPI CSV logging (reconcile)
                try:
                    ts_iso = (f.get("trade_time") or f.get("time") or datetime.now(timezone.utc).isoformat())
                    oid = f.get("order_id")
                    intent = self._intent.get(str(oid), {}) if oid else {}
                    intent_price = intent.get("intent_price")
                    reason_val = (intent.get("reason") or "")
                    entry_reason = reason_val if side == "BUY" else ""
                    exit_reason  = reason_val if side == "SELL" else ""
                    pnl_fill = None
                    if side == "SELL":
                        try:
                            sell_qty_csv = min(size, qty_before_snapshot)
                            pnl_fill = sell_qty_csv * (price - cb_before_snapshot) - fee
                        except Exception:
                            pnl_fill = None
                    hold_time_sec = None
                    try:
                        if side == "SELL" and self.positions[pid] == 0.0 and self._entry_time.get(pid):
                            hold_time_sec = max(0.0, time.time() - self._entry_time[pid])
                            self._entry_time[pid] = None
                    except Exception:
                        pass
                    self._append_trade_csv(ts_iso=ts_iso, order_id=str(oid) if oid else None, side=side, coin_id=pid,
                                           size=size, price=price, fee=fee,
                                           liquidity=f.get("liquidity_indicator"), pnl=pnl_fill,
                                           position_after=self.positions.get(pid),
                                           cost_basis_after=self.cost_basis.get(pid),
                                           intent_price=intent_price, hold_time_sec=hold_time_sec,
                                           entry_reason=entry_reason, exit_reason=exit_reason)
                except Exception as e:
                    logging.debug("CSV log (reconcile) failed: %s", e)

            changed = True

        if changed:
            # prune & save via helper
            # Swab shout (before cleanup)
            max_keys = int(getattr(self.cfg, "processed_fills_max", 10000))
            try:
                pre_entries = len(self._processed_fills.to_dict())
            except Exception:
                pre_entries = -1
            self._swab_log(f"Pruning processed fills (max={max_keys}, current_entries={pre_entries})…")
            # prune & save via helper
            self._processed_fills.prune(max_keys=max_keys)
            with self._state_lock:
                save_json(PROCESSED_FILLS_FILE, self._processed_fills.to_dict())
                self._save_portfolio()
            run_delta = self.realized_pnl - (getattr(self, "run_pnl_baseline", self.realized_pnl))
            pnl_str = f"{self.realized_pnl:.{PNL_DECIMALS}f}"
            run_str = f"{run_delta:.{PNL_DECIMALS}f}"
            logging.info("Reconciled fills. Lifetime P&L: $%s | This run: $%s", pnl_str, run_str)

    def _on_candle_close(self, coin_id: str, start_sec: int, close_price: float):
        # Update indicators once per closed candle
        s = self.short[coin_id].update(close_price)
        l = self.long[coin_id].update(close_price)
        if self.enable_advisors:
            self._macd[coin_id].update(close_price)
            self._rsi[coin_id].update(close_price)
        self.ticks[coin_id] += 1  # now counts candles
        # Count candles closed since last telemetry heartbeat
        try:
            self._candles_closed += 1
        except Exception:
            # very defensive; should not happen
            self._candles_closed = 1
        # Mark last closed-candle time for stall watchdog
        try:
            self._last_candle_close_ts[coin_id] = time.time()
        except Exception:
            pass

        # ---- Quartermaster pre-check (take-profit / time-in-trade) ----
        try:
            held_qty = max(0.0, float(self.positions.get(coin_id, 0.0)))
            # If local position is empty (e.g., buys were before our fills lookback),
            # peek at live balances so Quartermaster uses a real number.
            try:
                live_avail = float(self._get_live_available_base(coin_id))
            except Exception:
                live_avail = -1.0
            if live_avail >= 0.0:
                held_cache = held_qty
                held_qty = min(held_cache, live_avail)
                # If cache was empty but live shows a balance, seed cache
                if held_cache <= 0.0 and live_avail > 0.0:
                    self.positions[coin_id] = live_avail

            if held_qty > 0.0:
                entry_price = float(self.cost_basis.get(coin_id, 0.0) or 0.0)
                macd_hist = self._macd[coin_id].hist if self.enable_advisors else None
                entry_ts = self._entry_time.get(coin_id)
                hold_hours = 0.0
                if entry_ts:
                    hold_hours = max(0.0, (time.time() - float(entry_ts)) / 3600.0)

                qm_ok, qm_reason = _quartermaster_exit_ok(
                    self.cfg, last_price=close_price, entry_price=entry_price,
                    hold_hours=hold_hours, macd_hist=macd_hist
                )
                if qm_ok:
                    # cooldown + throttle + dust guard
                    cooldown_s = int(getattr(self.cfg, "per_coin_cooldown_s", getattr(self.cfg, "cooldown_sec", 300)))
                    now_ts = time.time()
                    # Dust/threshold suppression window honored
                    if now_ts < float(self._qm_dust_suppress_until[coin_id] or 0.0):
                        return
                    # Require at least one increment or min-market base size (optionally buffered)
                    try:
                        base_inc = float(self.base_inc.get(coin_id, 1e-8))
                    except Exception:
                        base_inc = 1e-8
                    min_mkt = float(self.min_market_base_size.get(coin_id, 0.0) or 0.0)
                    sell_floor = max(base_inc, min_mkt)
                    buffer_mult = 1.0  # keep exact-inc allowed; raise to 1.1 for extra safety if desired
                    sell_required = sell_floor * buffer_mult
                    if held_qty + 1e-18 < sell_required:
                        suppress_min = 30  # configurable if needed
                        self._qm_dust_suppress_until[coin_id] = now_ts + suppress_min * 60
                        logging.info(
                            "Quartermaster: suppressing SELL %s for %d min "
                            "(held=%.10f < required=%.10f, inc=%g, min_mkt=%.10f).",
                            coin_id, suppress_min, held_qty, sell_required, base_inc, min_mkt
                        )
                        return
                        
                    if not self.last.ok(coin_id, cooldown_s):
                        return
                    if now_ts - float(self._qm_last_ts[coin_id] or 0.0) < min(60, cooldown_s):
                        return
                    min_base = float(self.base_inc.get(coin_id, 1e-8))
                    if held_qty < (min_base * 0.99):
                        return

                    quote_usd = held_qty * close_price
                    logging.info("Quartermaster triggered SELL attempt %s: held=%.10f close=%.8f reason=%s",
                                 coin_id, held_qty, close_price, qm_reason)
                    # Actually place the exit order
                    self.place_order(coin_id, side="SELL", quote_usd=quote_usd,
                                     last_price=close_price, reason=qm_reason)
                    self._qm_last_ts[coin_id] = now_ts
                    return  # one decisive action per candle per coin
        except Exception as _e:
            logging.debug("Quartermaster check failed for %s: %s", coin_id, _e)

        min_needed = self.min_ticks_per_coin.get(coin_id, int(getattr(self.cfg, "min_candles", getattr(self.cfg, "min_ticks", 60))))
        if self.ticks[coin_id] >= min_needed and s is not None and l is not None:
            self.evaluate_signal(coin_id, close_price, s, l)

    def reconcile_now(self, hours: Optional[int] = None) -> None:
        """Idempotent, reentrant-safe sweep of recent fills."""
        if getattr(self, "_reconciling", False):
            logging.debug("Reconcile already running; skipping.")
            return
        self._reconciling = True
        try:
            if hours is not None:
                h = int(hours)
            else:
                # Use long lookback ONLY for startup or manual reconcile
                h = int(getattr(self.cfg, "lookback_hours", 48))
            # Clamp to 6–168h for safety
            h = max(1, min(h, 168))
            self.reconcile_recent_fills(h)
        except Exception as e:
            logging.exception("reconcile_now failed: %s", e)
        finally:
            self._reconciling = False

