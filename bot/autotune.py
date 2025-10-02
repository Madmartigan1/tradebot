# bot/autotune.py (v1.0.5 hybrid tuning + v1.0.4 18h vote; reuses CONFIG._rest)
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple, Optional
import csv, os

try:
    from coinbase.rest import RESTClient  # optional import; we won't construct here
except Exception:
    RESTClient = None  # type: ignore

# ---------------------------
# Lightweight math helpers
# ---------------------------
def _ema(seq: List[float], n: int) -> List[float]:
    if n <= 1 or len(seq) < n: return []
    k = 2.0 / (n + 1.0)
    out: List[float] = []
    ema_val = sum(seq[:n]) / n
    out.append(ema_val)
    for x in seq[n:]:
        ema_val = (x - ema_val) * k + ema_val
        out.append(ema_val)
    return out

def _macd_hist(prices: List[float], fast: int, slow: int, signal: int) -> List[float]:
    if len(prices) < slow + signal + 5: return []
    f = _ema(prices, fast); s = _ema(prices, slow)
    macd_line = [a - b for a, b in zip(f[-len(s):], s)]
    sig = _ema(macd_line, signal)
    return [m - si for m, si in zip(macd_line[-len(sig):], sig)]

def _granularity_enum(seconds: int) -> str:
    return {60:"ONE_MINUTE",300:"FIVE_MINUTE",900:"FIFTEEN_MINUTE",3600:"ONE_HOUR"}.get(seconds,"FIVE_MINUTE")

def _fetch_closes(rest, product_id: str, gran_sec: int, hours: int) -> List[float]:
    end = int(datetime.now(timezone.utc).timestamp())
    start = end - hours * 3600
    r = rest.get_candles(product_id=product_id, start=str(start), end=str(end), granularity=_granularity_enum(gran_sec))
    arr = (getattr(r, "to_dict", lambda: r)() or {}).get("candles", [])
    closes = []
    for c in arr:
        try: closes.append(float(c["close"]))
        except Exception: continue
    closes.sort()  # oldest→newest
    return closes

# ---------------------------
# Regime detection (v1.0.4 logic)
# ---------------------------
def detect_regime_for_prices(prices: List[float], deadband_bps: float = 8.0, macd=(12,26,9)) -> str:
    if len(prices) < 200: return "choppy"
    e40 = _ema(prices, 40); e120 = _ema(prices, 120)
    if not e40 or not e120: return "choppy"
    last = prices[-1]; e40 = e40[-1]; e120 = e120[-1]
    margin_bps = 0.0 if not (last and e40 and e120) else abs(e40 - e120) / last * 10_000.0
    hist = _macd_hist(prices, *macd)
    if not hist: return "choppy"
    pos_share = sum(1 for h in hist if h > 0) / max(1, len(hist))
    # chop score
    small_move_cnt = sum(1 for i in range(1, len(prices)) if abs(prices[i]/prices[i-1]-1.0)*10_000.0 < 2.0)
    small_move_share = small_move_cnt / max(1, (len(prices)-1))
    near_zero_hist = sum(1 for h in hist if last and abs(h/last)*10_000.0 < 2.0) / max(1, len(hist))
    chop_score = 0.5*small_move_share + 0.5*near_zero_hist
    if e40 > e120 and margin_bps >= 2*deadband_bps and pos_share >= 0.60 and chop_score < 0.45: return "uptrend"
    if e40 < e120 and margin_bps >= 2*deadband_bps and pos_share <= 0.40 and chop_score < 0.45: return "downtrend"
    return "choppy"

# ---------------------------
# CSV-driven product stats (3-day window)
# ---------------------------
@dataclass
class ProductStats:
    pnl_proxy_3d: float = 0.0
    trades_3d: int = 0

def _read_csv_3d_stats(csv_path: str) -> Dict[str, ProductStats]:
    out: Dict[str, ProductStats] = {}
    if not os.path.exists(csv_path): return out
    cutoff = datetime.now(timezone.utc) - timedelta(days=3)
    try:
        with open(csv_path, newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                try:
                    pid = row.get("product_id") or row.get("product") or ""
                    ts = row.get("closed_at") or row.get("timestamp") or ""
                    if not pid or not ts: continue
                    dt = datetime.fromisoformat(ts.replace("Z","+00:00"))
                    if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
                    if dt < cutoff: continue
                    pnl_bps = float(row.get("pnl_bps","0") or 0.0)
                    hit = out.setdefault(pid, ProductStats())
                    hit.pnl_proxy_3d += pnl_bps
                    hit.trades_3d += 1
                except Exception:
                    continue
    except Exception:
        pass
    return out

# ---------------------------
# Regime targets + clamps
# ---------------------------
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
    "choppy": V102_CHOPPY,
    "uptrend": {
        "confirm_candles": 2,
        "per_product_cooldown_s": 600,
        "rsi_buy_max": 65.0,
        "rsi_sell_min": 35.0,
        "macd_buy_min": 2.0,
        "macd_sell_max": -2.0,
        "ema_deadband_bps": 10.0,
    },
    "downtrend": {
        "confirm_candles": 3,
        "per_product_cooldown_s": 1200,
        "rsi_buy_max": 55.0,
        "rsi_sell_min": 45.0,
        "macd_buy_min": 4.0,
        "macd_sell_max": -4.0,
        "ema_deadband_bps": 7.0,
    },
}
CLAMPS_BY_REGIME: Dict[str, Dict[str, Tuple[Optional[float], Optional[float]]]] = {
    "choppy": {"confirm_candles":(2,5), "per_product_cooldown_s":(300,1800), "rsi_buy_max":(50,70),
               "rsi_sell_min":(30,50), "macd_buy_min":(0,8), "macd_sell_max":(-8,0), "ema_deadband_bps":(5,12)},
    "uptrend": {"confirm_candles":(1,4), "per_product_cooldown_s":(300,1200), "rsi_buy_max":(55,75),
                "rsi_sell_min":(25,45), "macd_buy_min":(0,6), "macd_sell_max":(-6,0), "ema_deadband_bps":(6,14)},
    "downtrend": {"confirm_candles":(2,5), "per_product_cooldown_s":(600,2400), "rsi_buy_max":(45,65),
                  "rsi_sell_min":(35,55), "macd_buy_min":(2,10), "macd_sell_max":(-10,-2), "ema_deadband_bps":(5,10)},
}
def _clamp_for(regime: str, name: str, value):
    lo, hi = CLAMPS_BY_REGIME.get(regime, {}).get(name, (None, None))
    if lo is None: return value
    try: return max(lo, min(hi, value))
    except Exception: return value

def _blend_clamp(name: str, r1: str, r2: str):
    lo1, hi1 = CLAMPS_BY_REGIME.get(r1, {}).get(name, (None, None))
    lo2, hi2 = CLAMPS_BY_REGIME.get(r2, {}).get(name, (None, None))
    if lo1 is None and lo2 is None: return None, None
    if lo1 is None: lo1 = lo2
    if lo2 is None: lo2 = lo1
    if hi1 is None: hi1 = hi2
    if hi2 is None: hi2 = hi1
    return max(lo1, lo2), min(hi1, hi2)

BLEND_KNOBS = {
    "confirm_candles", "per_product_cooldown_s",
    "rsi_buy_max", "rsi_sell_min",
    "macd_buy_min", "macd_sell_max",
    "ema_deadband_bps",
}

OFFSET_FLOOR_MAJOR = 12.0
OFFSET_FLOOR_OTHER = 16.0
OFFSET_CEIL = 40.0
OFFSET_FLOOR_GLOBAL = 6.0

# =========================
# Portfolio vote (v1.0.4) — REUSE existing authenticated client; do NOT construct a new one
# =========================
def _compute_portfolio_vote(cfg, api_key: str, api_secret: str) -> Dict[str, int]:
    hours = int(getattr(cfg, "autotune_lookback_hours", 18))
    gran_map = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}
    gran_sec = gran_map.get(str(getattr(cfg, "candle_interval", "5m")).lower(), 300)

    # 🚫 Do not build RESTClient here (avoids PEM parsing); reuse the one the bot built.
    rest = getattr(cfg, "_rest", None)

    vote = {"uptrend": 0, "downtrend": 0, "choppy": 0}
    deadband_bps = float(getattr(cfg, "ema_deadband_bps", 8.0))

    for pid in getattr(cfg, "product_ids", []):
        try:
            prices = _fetch_closes(rest, pid, gran_sec, hours) if rest else []
        except Exception:
            prices = []
        regime = detect_regime_for_prices(prices, deadband_bps=deadband_bps)
        vote[regime] = vote.get(regime, 0) + 1
    return vote

# =======================================
# v1.0.5: Hybrid mixer + detailed summary
# =======================================
def autotune_config(cfg, api_key: str, api_secret: str, portfolio_id: Optional[str] = None, preview_only: bool = False):
    portfolio_vote = _compute_portfolio_vote(cfg, api_key=api_key, api_secret=api_secret)
    total = max(1, sum(portfolio_vote.values()))
    winner, votes = max(portfolio_vote.items(), key=lambda kv: kv[1])
    share = votes / total

    if share >= 0.70:
        mode = "SNAP"; portfolio_regime = winner; alpha = 1.0
    elif 0.55 <= share <= 0.69:
        mode = "BLEND"; portfolio_regime = winner
        alpha = max(0.0, min(1.0, (share - 0.55) / (0.69 - 0.55)))
    else:
        mode = "CHOPPY"; portfolio_regime = "choppy"; alpha = 0.0

    if mode == "SNAP":
        targets = REGIME_TARGETS.get(portfolio_regime, REGIME_TARGETS["choppy"])
    elif mode == "CHOPPY":
        targets = REGIME_TARGETS["choppy"]
    else:
        win_t = REGIME_TARGETS.get(winner, REGIME_TARGETS["choppy"])
        cho_t = REGIME_TARGETS["choppy"]
        targets = {}
        for k in set(win_t.keys()) | set(cho_t.keys()):
            v_win = win_t.get(k, cho_t.get(k))
            v_cho = cho_t.get(k, win_t.get(k, v_win))
            if k in BLEND_KNOBS and isinstance(v_win, (int, float)) and isinstance(v_cho, (int, float)):
                v = (alpha * v_win) + ((1.0 - alpha) * v_cho)
                lo, hi = _blend_clamp(k, winner, "choppy")
                if lo is not None: v = max(lo, min(hi, v))
                if isinstance(v_win, int) or isinstance(v_cho, int) or k in {"confirm_candles","per_product_cooldown_s"}:
                    v = int(round(v))
                targets[k] = v
            else:
                targets[k] = v_win

    changes: Dict[str, Tuple[Optional[float], Optional[float]]] = {}

    def set_if(name: str, wanted):
        new_val = wanted if (mode == "BLEND" and name in BLEND_KNOBS) else _clamp_for(portfolio_regime, name, wanted)
        old = getattr(cfg, name, None)
        if not preview_only: setattr(cfg, name, new_val)
        return old, new_val

    for k, v in targets.items(): changes[k] = set_if(k, v)

    allow_disabling = (mode == "SNAP" and portfolio_regime != "choppy")
    disabled: List[str] = []

    offsets: Dict[str, float] = dict(getattr(cfg, "maker_offset_bps_per_product", {}) or {})
    default_off = float(getattr(cfg, "maker_offset_bps", 5.0))
    kpi = _read_csv_3d_stats(os.path.join(".state", "trades.csv"))

    majors = set(getattr(cfg, "majors", {"BTC-USD","ETH-USD","SOL-USD","XRP-USD","DOGE-USD"}))
    floor_major = float(getattr(cfg, "maker_offset_floor_major_bps", 12.0))
    floor_other = float(getattr(cfg, "maker_offset_floor_other_bps", 16.0))
    ceil_all = float(getattr(cfg, "maker_offset_ceil_bps", 40.0))

    for pid in getattr(cfg, "product_ids", []):
        base = float(offsets.get(pid, default_off))
        st = kpi.get(pid)
        if st and st.trades_3d > 0:
            wr = st.pnl_proxy_3d >= 0.0
            base += 1.0 if wr else -1.0
        fl = floor_major if pid in majors else floor_other
        base = max(fl, min(ceil_all, base))
        base = max(6.0, base)
        offsets[pid] = float(int(round(base)))
        if allow_disabling and (not st or st.trades_3d < 1):
            pass  # advisory only

    if not preview_only:
        setattr(cfg, "maker_offset_bps_per_product", offsets)
        setattr(cfg, "products_disabled", sorted(disabled))

    knob_changes = {k: {"old": ov[0], "new": ov[1]} for k, ov in changes.items()}
    return {
        "mode": mode,
        "winner": winner,
        "share": round(share, 4),
        "alpha": round(alpha, 4),
        "portfolio_vote": {k:int(v) for k,v in (portfolio_vote or {}).items()},
        "portfolio_regime": portfolio_regime,
        "knob_changes": knob_changes,
        "global_changes": {k: f"{ov[0]}→{ov[1]}" for k, ov in changes.items()},
        "disabled_products": sorted(disabled),
        "offsets_changed": {k: offsets[k] for k in offsets},
    }
