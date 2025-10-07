# bot/autotune.py (v1.0.7) — telemetry + hybrid tuning + caller-controlled lookback
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
    if n <= 1 or len(seq) < n:
        return []
    k = 2.0 / (n + 1.0)
    out: List[float] = []
    ema_val = sum(seq[:n]) / n
    out.append(ema_val)
    for x in seq[n:]:
        ema_val = (x - ema_val) * k + ema_val
        out.append(ema_val)
    return out


def _macd_hist(prices: List[float], fast: int, slow: int, signal: int) -> List[float]:
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


def _parse_ts_to_epoch(t) -> int:
    # Coinbase candles often have "start" epoch seconds; sometimes ISO strings.
    try:
        return int(t)
    except Exception:
        try:
            return int(datetime.fromisoformat(str(t).replace("Z", "+00:00")).timestamp())
        except Exception:
            return 0


def _fetch_closes(rest, product_id: str, gran_sec: int, hours: int) -> List[float]:
    end = int(datetime.now(timezone.utc).timestamp())
    start = end - hours * 3600
    r = rest.get_candles(
        product_id=product_id,
        start=str(start),
        end=str(end),
        granularity=_granularity_enum(gran_sec),
    )
    arr = (getattr(r, "to_dict", lambda: r)() or {}).get("candles", [])
    # Sort by time (oldest → newest); do NOT sort by price.
    def _ts(c):
        return _parse_ts_to_epoch(c.get("start") or c.get("time") or c.get("timestamp"))

    arr.sort(key=_ts)
    return [float(c["close"]) for c in arr if "close" in c]


# ---------------------------
# Regime detection (v1.0.4 logic)
# ---------------------------
def detect_regime_for_prices(prices: List[float], deadband_bps: float = 8.0, macd=(12, 26, 9)) -> str:
    if len(prices) < 120:
        return "choppy"
    e40 = _ema(prices, 40)
    e120 = _ema(prices, 120)
    if not e40 or not e120:
        return "choppy"
    last = prices[-1]
    e40 = e40[-1]
    e120 = e120[-1]
    margin_bps = 0.0 if not (last and e40 and e120) else abs(e40 - e120) / last * 10_000.0
    hist = _macd_hist(prices, *macd)
    if not hist:
        return "choppy"
    pos_share = sum(1 for h in hist if h > 0) / max(1, len(hist))
    # chop score
    small_move_cnt = sum(1 for i in range(1, len(prices)) if abs(prices[i] / prices[i - 1] - 1.0) * 10_000.0 < 2.0)
    small_move_share = small_move_cnt / max(1, (len(prices) - 1))
    near_zero_hist = sum(1 for h in hist if last and abs(h / last) * 10_000.0 < 2.0) / max(1, len(hist))
    chop_score = 0.5 * small_move_share + 0.5 * near_zero_hist
    if e40 > e120 and margin_bps >= 2 * deadband_bps and pos_share >= 0.60 and chop_score < 0.45:
        return "uptrend"
    if e40 < e120 and margin_bps >= 2 * deadband_bps and pos_share <= 0.40 and chop_score < 0.45:
        return "downtrend"
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
    if not os.path.exists(csv_path):
        return out
    cutoff = datetime.now(timezone.utc) - timedelta(days=3)
    try:
        with open(csv_path, newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                try:
                    pid = row.get("product_id") or row.get("product") or ""
                    ts = row.get("closed_at") or row.get("timestamp") or ""
                    if not pid or not ts:
                        continue
                    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt < cutoff:
                        continue
                    pnl_bps = float(row.get("pnl_bps", "0") or 0.0)
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
    "per_product_cooldown_s": 600,
    "rsi_buy_max": 65.0,
    "rsi_sell_min": 35.0,
    "macd_buy_min": 2.0,
    "macd_sell_max": -2.0,
    "ema_deadband_bps": 6.0,
}
REGIME_TARGETS = {
    "choppy": V102_CHOPPY,
    "uptrend": {
        "confirm_candles": 2,
        "per_product_cooldown_s": 420,
        "rsi_buy_max": 72.0,
        "rsi_sell_min": 40.0,
        "macd_buy_min": 1.5,
        "macd_sell_max": -2.5,
        "ema_deadband_bps": 5.0,
    },
    "downtrend": {
        "confirm_candles": 2,
        "per_product_cooldown_s": 900,
        "rsi_buy_max": 60.0,
        "rsi_sell_min": 30.0,
        "macd_buy_min": 2.5,
        "macd_sell_max": -1.5,
        "ema_deadband_bps": 5.0,
    },
}
CLAMPS_BY_REGIME: Dict[str, Dict[str, Tuple[Optional[float], Optional[float]]]] = {
    "choppy": {
        "confirm_candles": (2, 5),
        "per_product_cooldown_s": (300, 1800),
        "rsi_buy_max": (60, 70),
        "rsi_sell_min": (30, 40),
        "macd_buy_min": (1.0, 3.0),
        "macd_sell_max": (-3.0, -1.0),
        "ema_deadband_bps": (5.0, 8.0),
    },
    "uptrend": {
        "confirm_candles": (1, 3),
        "per_product_cooldown_s": (300, 1200),
        "rsi_buy_max": (65, 75),
        "rsi_sell_min": (35, 45),
        "macd_buy_min": (1.0, 3.0),
        "macd_sell_max": (-3.0, -1.0),
        "ema_deadband_bps": (4.5, 8.0),
    },
    "downtrend": {
        "confirm_candles": (1, 3),
        "per_product_cooldown_s": (300, 1200),
        "rsi_buy_max": (55, 65),
        "rsi_sell_min": (25, 40),
        "macd_buy_min": (2.0, 4.0),
        "macd_sell_max": (-2.5, -1.0),
        "ema_deadband_bps": (4.5, 8.0),
    },
}


def _clamp_for(regime: str, name: str, value):
    lo, hi = CLAMPS_BY_REGIME.get(regime, {}).get(name, (None, None))
    if lo is None:
        return value
    try:
        return max(lo, min(hi, value))
    except Exception:
        return value


def _blend_clamp(name: str, r1: str, r2: str):
    lo1, hi1 = CLAMPS_BY_REGIME.get(r1, {}).get(name, (None, None))
    lo2, hi2 = CLAMPS_BY_REGIME.get(r2, {}).get(name, (None, None))
    if lo1 is None and lo2 is None:
        return None, None
    if lo1 is None:
        lo1 = lo2
    if lo2 is None:
        lo2 = lo1
    if hi1 is None:
        hi1 = hi2
    if hi2 is None:
        hi2 = hi1
    return max(lo1, lo2), min(hi1, hi2)


BLEND_KNOBS = {
    "confirm_candles",
    "per_product_cooldown_s",
    "rsi_buy_max",
    "rsi_sell_min",
    "macd_buy_min",
    "macd_sell_max",
    "ema_deadband_bps",
}

OFFSET_FLOOR_MAJOR = 12.0
OFFSET_FLOOR_OTHER = 16.0
OFFSET_CEIL = 40.0
OFFSET_FLOOR_GLOBAL = 6.0


# =========================
# Portfolio vote (v1.0.4) — REUSE existing authenticated client; do NOT construct a new one
# =========================
def _compute_portfolio_vote(
    cfg,
    api_key: str,
    api_secret: str,
    lookback_hours_override: Optional[int] = None,
) -> Tuple[Dict[str, int], Dict[str, int]]:
    """Returns (vote_counts, meta)
    meta contains the exact hours / granularity used to help logging.
    """
    # Base lookback (shared knob), possibly bumped by min-candles requirement
    hours_base = int(
        lookback_hours_override
        if (lookback_hours_override is not None and lookback_hours_override > 0)
        else getattr(cfg, "autotune_lookback_hours", 18)
    )

    vote_map = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}
    # Use the dedicated vote interval (decoupled from trading candles)
    gran_sec = vote_map.get(str(getattr(cfg, "autotune_vote_interval", "15m")).lower(), 900)
    # Ensure the regime detector has enough samples (you set 72 by default for ~18h @15m)
    min_candles = int(getattr(cfg, "autotune_vote_min_candles", 72))
    need_hours = int((min_candles * gran_sec + 3599) // 3600)  # ceil division in hours
    hours = max(hours_base, need_hours)

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

    meta = {
        "hours": hours,
        "hours_base": hours_base,
        "granularity_sec": gran_sec,
        "min_candles": min_candles,
    }
    return vote, meta


# ===================================================
# v1.0.8: Hybrid mixer + detailed summary + Telemetry + lookback override
# ===================================================
def autotune_config(
    cfg,
    api_key: str,
    api_secret: str,
    portfolio_id: Optional[str] = None,
    preview_only: bool = False,
    lookback_hours_override: Optional[int] = None,
):
    portfolio_vote, meta = _compute_portfolio_vote(
        cfg, api_key=api_key, api_secret=api_secret, lookback_hours_override=lookback_hours_override
    )
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
                if lo is not None:
                    v = max(lo, min(hi, v))
                if isinstance(v_win, int) or isinstance(v_cho, int) or k in {"confirm_candles", "per_product_cooldown_s"}:
                    v = int(round(v))
                targets[k] = v
            else:
                targets[k] = v_win

    changes: Dict[str, Tuple[Optional[float], Optional[float]]] = {}

    def set_if(name: str, wanted):
        new_val = wanted if (mode == "BLEND" and name in BLEND_KNOBS) else _clamp_for(portfolio_regime, name, wanted)
        old = getattr(cfg, name, None)
        if not preview_only:
            setattr(cfg, name, new_val)
        return old, new_val

    for k, v in targets.items():
        changes[k] = set_if(k, v)

    # Only matters if you ever flip preview_only=False
    allow_disabling = (mode == "SNAP" and portfolio_regime != "choppy")

    disabled: List[str] = []
    disabled_reasons: Dict[str, str] = {}
    offsets: Dict[str, float] = dict(getattr(cfg, "maker_offset_bps_per_product", {}) or {})
    default_off = float(getattr(cfg, "maker_offset_bps", 5.0))
    kpi = _read_csv_3d_stats(os.path.join(".state", "trades.csv"))
    startup_kpi_empty = (len(kpi) == 0)  # suppress noisy 'no_kpi' telemetry on cold start

    majors = set(getattr(cfg, "majors", {"BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD"}))
    floor_major = float(getattr(cfg, "maker_offset_floor_major_bps", OFFSET_FLOOR_MAJOR))
    floor_other = float(getattr(cfg, "maker_offset_floor_other_bps", OFFSET_FLOOR_OTHER))
    ceil_all = float(getattr(cfg, "maker_offset_ceil_bps", OFFSET_CEIL))

    for pid in getattr(cfg, "product_ids", []):
        base = float(offsets.get(pid, default_off))
        st = kpi.get(pid)
        if st and st.trades_3d > 0:
            wr = st.pnl_proxy_3d >= 0.0
            base += 1.0 if wr else -1.0
        fl = floor_major if pid in majors else floor_other
        base = max(fl, min(ceil_all, base))
        base = max(OFFSET_FLOOR_GLOBAL, base)  # global floor
        offsets[pid] = float(int(round(base)))

        # --- Telemetry only: flag candidates; nothing actually disabled here ---
        if not startup_kpi_empty:
            if (not st or st.trades_3d < 1):
                if pid not in disabled:
                    disabled.append(pid)
                reason = "inactive_3d" if st else "no_kpi"
                disabled_reasons[pid] = reason
            elif st.trades_3d >= 4 and st.pnl_proxy_3d <= -10.0 and pid not in majors:
                if pid not in disabled:
                    disabled.append(pid)
                disabled_reasons[pid] = f"neg_pnl_3d_bps={st.pnl_proxy_3d:.1f},trades={st.trades_3d}"

    if not preview_only:
        # Apply tuned offsets always
        setattr(cfg, "maker_offset_bps_per_product", offsets)
        # Only actually "disable" if SNAP & non-choppy
        if allow_disabling:
            setattr(cfg, "products_disabled", sorted(disabled))

    knob_changes = {k: {"old": ov[0], "new": ov[1]} for k, ov in changes.items()}
    return {
        "mode": mode,
        "winner": winner,
        "share": round(share, 4),
        "alpha": round(alpha, 4),
        "portfolio_vote": {k: int(v) for k, v in (portfolio_vote or {}).items()},
        "portfolio_regime": portfolio_regime,
        "vote_meta": meta,  # <-- exposes hours/granularity/min_candles used
        "knob_changes": knob_changes,
        "global_changes": {k: f"{ov[0]}→{ov[1]}" for k, ov in changes.items()},
        "disabled_products": sorted(disabled),            # telemetry, shown by main if non-empty
        "disabled_details": {k: disabled_reasons[k] for k in sorted(disabled_reasons)},
        "offsets_changed": {k: offsets[k] for k in offsets},
    }
