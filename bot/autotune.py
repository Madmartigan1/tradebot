# bot/autotune.py  (v1.0.4-choppy-profile)
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple, Optional
import csv, os

try:
    from coinbase.rest import RESTClient
except Exception:
    RESTClient = None  # type: ignore

# ---------------------------
# Lightweight math helpers
# ---------------------------
def _ema(seq: List[float], n: int) -> List[float]:
    if not seq:
        return []
    mult = 2.0 / (n + 1.0)
    out = []
    v = None
    for x in seq:
        v = x if v is None else (x - v) * mult + v
        out.append(v)
    return out

def _macd_hist(prices: List[float], fast=12, slow=26, signal=9) -> List[float]:
    if len(prices) < slow + signal + 5:
        return []
    f = _ema(prices, fast)
    s = _ema(prices, slow)
    macd_line = [a - b for a, b in zip(f[-len(s):], s)]
    sig = _ema(macd_line, signal)
    return [m - si for m, si in zip(macd_line[-len(sig):], sig)]

def _granularity_enum(seconds: int) -> str:
    return {
        60: "ONE_MINUTE",
        300: "FIVE_MINUTE",
        900: "FIFTEEN_MINUTE",
        3600: "ONE_HOUR",
    }.get(seconds, "FIVE_MINUTE")

def _fetch_closes(rest: RESTClient, product_id: str, gran_sec: int, hours: int) -> List[float]:
    end = int(datetime.now(timezone.utc).timestamp())
    start = end - hours * 3600
    r = rest.get_candles(
        product_id=product_id,
        start=str(start),
        end=str(end),
        granularity=_granularity_enum(gran_sec),
    )
    arr = (getattr(r, "to_dict", lambda: r)() or {}).get("candles", [])
    closes = []
    for c in arr:
        try:
            closes.append(float(c["close"]))
        except Exception:
            pass
    closes.reverse()  # Coinbase returns latest-first; we want oldest-first
    return closes

# ---------------------------
# Regime detection (per-asset)
# ---------------------------
def detect_regime_for_prices(
    prices: List[float],
    deadband_bps: float,
    macd=(12, 26, 9),
) -> str:
    if len(prices) < 130:  # enough to stabilize EMA120+signal
        return "choppy"
    ema40 = _ema(prices, 40)
    ema120 = _ema(prices, 120)
    last = prices[-1]
    e40 = ema40[-1]
    e120 = ema120[-1]
    margin_bps = 0.0 if not (last and e40 and e120) else abs(e40 - e120) / last * 10_000.0
    hist = _macd_hist(prices, *macd)
    if not hist:
        return "choppy"
    pos_share = sum(1 for h in hist if h > 0) / max(1, len(hist))
    # "chop score": tiny returns + near-zero MACD
    small_move_share = 0.0
    for i in range(1, len(prices)):
        r = abs(prices[i] / prices[i - 1] - 1.0) * 10_000.0
        if r < 2.0:
            small_move_share += 1
    small_move_share /= max(1, (len(prices) - 1))
    near_zero_hist = sum(1 for h in hist if abs(h / last) * 10_000.0 < 2.0) / max(1, len(hist))
    chop_score = 0.5 * small_move_share + 0.5 * near_zero_hist
    if e40 > e120 and margin_bps >= 2 * deadband_bps and pos_share >= 0.60 and chop_score < 0.45:
        return "uptrend"
    if e40 < e120 and margin_bps >= 2 * deadband_bps and pos_share <= 0.40 and chop_score < 0.45:
        return "downtrend"
    return "choppy"

# ---------------------------
# CSV-driven product stats (3-day window), no pandas required
# ---------------------------
@dataclass
class ProductStats:
    pnl_proxy_3d: float = 0.0
    trades_3d: int = 0
    maker_share_3d: Optional[float] = None

def _read_csv_3d_stats(csv_path: str) -> Dict[str, ProductStats]:
    stats: Dict[str, ProductStats] = {}
    if not os.path.exists(csv_path):
        return stats
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=3)
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                ts = row.get("ts") or row.get("timestamp") or row.get("time") or ""
                ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None
                if ts_dt is None:
                    continue
                if ts_dt.tzinfo is None:
                    ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                if ts_dt < start:
                    continue
                pid = row.get("product_id") or row.get("product") or ""
                side = (row.get("side") or "").upper()
                price = float(row.get("price") or 0.0)
                size = float(row.get("size") or 0.0)
                fee = float(row.get("fee") or 0.0)
                maker_raw = (row.get("is_maker") or row.get("maker") or "").strip().lower()
                maker = True if maker_raw in ("true", "1", "yes", "y") else False if maker_raw in ("false", "0", "no", "n") else None
                if not pid or size <= 0 or price <= 0:
                    continue
                s = stats.setdefault(pid, ProductStats())
                s.trades_3d += 1
                # crude P&L proxy: sum(sell proceeds) - sum(buy cost) - fees
                if side == "SELL":
                    s.pnl_proxy_3d += price * size
                elif side == "BUY":
                    s.pnl_proxy_3d -= price * size
                s.pnl_proxy_3d -= fee
                if maker is not None:
                    if s.maker_share_3d is None:
                        s.maker_share_3d = 1.0 if maker else 0.0
                    else:
                        prev_count = s.trades_3d - 1
                        s.maker_share_3d = (s.maker_share_3d * prev_count + (1.0 if maker else 0.0)) / s.trades_3d
            except Exception:
                continue
    return stats

# ---------------------------
# Regime→targets + safety rails
# ---------------------------

# Your v1.0.2 knobs for CHOPPY (the ones that printed money)
V102_CHOPPY = {
    "confirm_candles": 3,
    "per_product_cooldown_s": 900,
    "rsi_buy_max": 60.0,
    "rsi_sell_min": 40.0,
    "macd_buy_min": 3.0,
    "macd_sell_max": -3.0,
    "ema_deadband_bps": 8.0,
}

REGIME_TARGETS = {
    # Use v1.0.2 profile verbatim in choppy
    "choppy": V102_CHOPPY,
    # Keep reasonable defaults elsewhere (you can tune later)
    "uptrend": {
        "confirm_candles": 2,
        "per_product_cooldown_s": 600,
        "rsi_buy_max": 62.0,
        "rsi_sell_min": 38.0,
        "macd_buy_min": 2.5,
        "macd_sell_max": -2.5,
        "ema_deadband_bps": 6.0,
    },
    "downtrend": {
        "confirm_candles": 2,
        "per_product_cooldown_s": 900,
        "rsi_buy_max": 50.0,
        "rsi_sell_min": 42.0,
        "macd_buy_min": 6.0,
        "macd_sell_max": 0.0,
        "ema_deadband_bps": 8.0,
    },
}

# Clamp ranges — we PIN choppy to v1.0.2 so AutoTune can’t over-tighten it.
CLAMPS_BY_REGIME = {
    "choppy": {
        "confirm_candles": (3, 3),
        "per_product_cooldown_s": (900, 900),
        "rsi_buy_max": (60.0, 60.0),
        "rsi_sell_min": (40.0, 40.0),
        "macd_buy_min": (3.0, 3.0),
        "macd_sell_max": (-3.0, -3.0),
        "ema_deadband_bps": (8.0, 8.0),
    },
    # softer rails elsewhere (feel free to adjust later)
    "uptrend": {
        "confirm_candles": (2, 3),
        "per_product_cooldown_s": (600, 900),
        "rsi_buy_max": (60.0, 66.0),
        "rsi_sell_min": (34.0, 42.0),
        "macd_buy_min": (2.0, 4.0),
        "macd_sell_max": (-4.0, -2.0),
        "ema_deadband_bps": (4.0, 8.0),
    },
    "downtrend": {
        "confirm_candles": (2, 3),
        "per_product_cooldown_s": (600, 1200),
        "rsi_buy_max": (48.0, 55.0),
        "rsi_sell_min": (40.0, 46.0),
        "macd_buy_min": (4.0, 8.0),
        "macd_sell_max": (-1.0, 1.0),
        "ema_deadband_bps": (6.0, 10.0),
    },
}

def _clamp_for(regime: str, name: str, value):
    lo, hi = CLAMPS_BY_REGIME.get(regime, {}).get(name, (None, None))
    if lo is None:
        return value
    # numeric only
    try:
        return max(lo, min(hi, value))
    except Exception:
        return value

# ---------------------------
# Main autotune entrypoint
# ---------------------------
MAJORS = {"ETH-USD", "SOL-USD", "NEAR-USD", "HBAR-USD", "XLM-USD", "LINK-USD"}
TIER_A_FLOOR = 14.0
TIER_OTHER_FLOOR = 16.0
OFFSET_CEIL = 26.0
OFFSET_FLOOR_GLOBAL = 6.0  # keep offsets sane globally

def autotune_config(cfg, api_key: str, api_secret: str, portfolio_id: Optional[str] = None, preview_only: bool = False):
    # 1) Detect regime per product from candles
    hours = int(getattr(cfg, "autotune_lookback_hours", 18))
    gran_map = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}
    gran_sec = gran_map.get(str(getattr(cfg, "candle_interval", "5m")).lower(), 300)

    regimes: Dict[str, str] = {}
    portfolio_vote = {"uptrend": 0, "downtrend": 0, "choppy": 0}
    rest = RESTClient(api_key=api_key, api_secret=api_secret, rate_limit_headers=True) if RESTClient else None
    for pid in getattr(cfg, "product_ids", []):
        prices = _fetch_closes(rest, pid, gran_sec, hours) if rest else []
        r = detect_regime_for_prices(prices, float(getattr(cfg, "ema_deadband_bps", 8.0)))
        regimes[pid] = r
        portfolio_vote[r] = portfolio_vote.get(r, 0) + 1

    total = max(1, sum(portfolio_vote.values()))
    winner, votes = max(portfolio_vote.items(), key=lambda kv: kv[1])
    portfolio_regime = winner if (votes / total) >= 0.60 else "choppy"  # hysteresis

    # 2) Apply regime targets with clamps
    targets = REGIME_TARGETS.get(portfolio_regime, REGIME_TARGETS["choppy"])

    def set_if(name, wanted):
        new_val = _clamp_for(portfolio_regime, name, wanted)
        old = getattr(cfg, name, None)
        if not preview_only:
            setattr(cfg, name, new_val)
        return (old, new_val)

    changes: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
    for k, v in targets.items():
        changes[k] = set_if(k, v)

    # 3) Product gating + offset nudges from CSV (last 3 days)
    csv_path = os.path.join(".state", "trades.csv")
    stats = _read_csv_3d_stats(csv_path)
    offsets: Dict[str, float] = dict(getattr(cfg, "maker_offset_bps_per_product", {}))
    default_off = float(getattr(cfg, "maker_offset_bps", 5.0))
    disabled: List[str] = []

    # In choppy, DO NOT disable products (regain turnover); elsewhere keep your rule
    allow_disabling = (portfolio_regime != "choppy")

    for pid in getattr(cfg, "product_ids", []):
        base = offsets.get(pid, default_off)
        # global sanity floor
        base = max(OFFSET_FLOOR_GLOBAL, base)
        floor = TIER_A_FLOOR if pid in MAJORS else TIER_OTHER_FLOOR
        s = stats.get(pid)

        if allow_disabling and s and (s.pnl_proxy_3d <= -3.0) and (s.trades_3d >= 3) and (pid not in MAJORS):
            disabled.append(pid)

        new_off = base
        if pid not in disabled and s:
            maker_ok = (s.maker_share_3d is None) or (s.maker_share_3d >= 0.60)
            if s.pnl_proxy_3d > 2.0 and maker_ok:
                new_off = max(floor, base - 1.0)
            elif s.pnl_proxy_3d < 0.0 or (s.maker_share_3d is not None and s.maker_share_3d < 0.40):
                new_off = min(OFFSET_CEIL, base + 1.0)

        # final clamp
        new_off = max(OFFSET_FLOOR_GLOBAL, min(OFFSET_CEIL, new_off))
        offsets[pid] = new_off

    if not preview_only:
        cfg.maker_offset_bps_per_product.update(offsets)
        # keep visibility only; not enforced elsewhere
        setattr(cfg, "products_disabled", sorted(disabled))

    # 4) Return a compact summary for logging
    return {
        "portfolio_vote": portfolio_vote,
        "portfolio_regime": portfolio_regime,
        "global_changes": {k: f"{ov[0]}→{ov[1]}" for k, ov in changes.items()},
        "disabled_products": sorted(disabled),
        "offsets_changed": {k: offsets[k] for k in offsets},
    }
