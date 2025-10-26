# bot/autotune.py (v1.1.2) — telemetry + better BLEND tuning + caller-controlled lookback
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

    # only last 3 days
    cutoff = datetime.now(timezone.utc) - timedelta(days=3)

    def _to_dt(ts: str) -> Optional[datetime]:
        if not ts:
            return None
        try:
            # support "YYYY-mm-ddTHH:MM:SSZ" and ISO with tz
            ts2 = str(ts).strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts2)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None

    try:
        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                # Headers you keep:
                # ts, order_id, side, product, size, price, quote_usd, fee, liquidity, pnl,
                # position_after, cost_basis_after, intent_price, slippage_abs, slippage_bps,
                # hold_time_sec, entry_reason, exit_reason
                pid = (row.get("product") or "").strip()
                ts = (row.get("ts") or "").strip()
                if not pid or not ts:
                    continue

                dt = _to_dt(ts)
                if not dt or dt < cutoff:
                    continue

                try:
                    pnl_abs = float(row.get("pnl") or 0.0)
                    quote_usd = float(row.get("quote_usd") or 0.0)
                except ValueError:
                    pnl_abs, quote_usd = 0.0, 0.0

                pnl_bps = (pnl_abs / quote_usd) * 10_000.0 if quote_usd > 0 else 0.0

                hit = out.setdefault(pid, ProductStats())
                hit.pnl_proxy_3d += pnl_bps
                hit.trades_3d += 1
    except Exception:
        # keep autotune resilient even if CSV is in flux
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
        "ema_deadband_bps": 8.0,
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

# ---------------------------
# Stronger BLEND helpers
# ---------------------------
# Visible step size (bps) to make changes meaningful in logs/behavior
_QUANTUM_BPS = 0.5
# Ignore tiny moves below this (bps)
_MIN_VISIBLE_BPS = 0.25
# Per-vote safety cap (bps)
_MAX_DELTA_PER_VOTE_BPS = 2.0
# Per-knob weights: faster/slower “learning rates”
_KNOB_WEIGHT = {
    "ema_deadband_bps": 1.0,   # most responsive
    "macd_buy_min":     0.6,
    "macd_sell_max":    0.6,
    "rsi_buy_max":      0.5,
    "rsi_sell_min":     0.5,
    "confirm_candles":  0.3,   # slowest
    "per_product_cooldown_s": 0.4,
}

def _alpha_from_share(winner_share: float) -> float:
    """
    Map winner share in [0,1] to a blending factor alpha in [0,1].
    More decisive once share > 0.55; zero when share <= 0.5.
    """
    if winner_share <= 0.50:
        return 0.0
    if winner_share < 0.55:
        return 0.15
    # ramp up quicker after 0.55; clamp at 1.0
    return min(1.0, 0.15 + (winner_share - 0.55) * 1.7)

def _quantize_bps(x: float) -> float:
    return round(x / _QUANTUM_BPS) * _QUANTUM_BPS

def _apply_knob_blend(cur: float, target: float, alpha: float, knob_name: str) -> float:
    """
    Blend toward target, apply knob weight, cap per-vote delta, and quantize.
    Units are 'bps' for bps-style knobs; integer knobs will be rounded later.
    """
    w = _KNOB_WEIGHT.get(knob_name, 1.0)
    blended = cur + (target - cur) * alpha * w
    # cap per-vote change to avoid big jumps
    delta = max(-_MAX_DELTA_PER_VOTE_BPS, min(_MAX_DELTA_PER_VOTE_BPS, blended - cur))
    proposed = cur + delta
    q = _quantize_bps(proposed)
    # ignore float dust
    if abs(q - cur) < _MIN_VISIBLE_BPS:
        return cur
    return q


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
# v1.0.9: Hybrid mixer + detailed summary + Telemetry + lookback override
# ===================================================
def autotune_config(
    cfg,
    api_key: str,
    api_secret: str,
    portfolio_id: Optional[str] = None,
    preview_only: bool = False,
    lookback_hours_override: Optional[int] = None,
    rest: Optional["RESTClient"] = None,  # accept injected REST client
):
    # If a REST client is provided (older callers may pass rest=bot.rest),
    # expose it to the existing vote path which expects cfg._rest.
    if rest is not None:
        try:
            setattr(cfg, "_rest", rest)
        except Exception:
            pass
    portfolio_vote, meta = _compute_portfolio_vote(
        cfg, api_key=api_key, api_secret=api_secret, lookback_hours_override=lookback_hours_override
    )
    total = max(1, sum(portfolio_vote.values()))
    winner, votes = max(portfolio_vote.items(), key=lambda kv: kv[1])
    share = votes / total

    # Decide mode and blending strength
    if share >= 0.70:
        mode = "SNAP"; portfolio_regime = winner; alpha = 1.0
    elif share >= 0.55:
        mode = "BLEND"; portfolio_regime = winner
        # old linear map replaced by a more decisive curve
        alpha = _alpha_from_share(share)
    else:
        mode = "CHOPPY"; portfolio_regime = "choppy"; alpha = 0.0
        
    # Near-SNAP blends: use midpoint target between winner and choppy (gentler, reduces oscillation)
    blend_midpoint = (mode == "BLEND" and 0.65 <= share < 0.70)

    # Build target knob values
    changes: Dict[str, Tuple[Optional[float], Optional[float]]] = {}

    def _apply_and_record(name: str, wanted):
        # For non-BLEND, clamp by the portfolio_regime band.
        new_val = wanted if (mode == "BLEND" and name in BLEND_KNOBS) else _clamp_for(portfolio_regime, name, wanted)
        old = getattr(cfg, name, None)
        if not preview_only:
            setattr(cfg, name, new_val)
        return old, new_val

    if mode in ("SNAP", "CHOPPY"):
        targets = REGIME_TARGETS.get(portfolio_regime, REGIME_TARGETS["choppy"])
        for k, v in targets.items():
            changes[k] = _apply_and_record(k, v)

    else:
        # BLEND: move current cfg toward the WINNER preset (quantized, capped), then clamp to the
        # overlap band between winner & choppy so we never exceed either regime’s safe envelope.
        win_t = REGIME_TARGETS.get(winner, REGIME_TARGETS["choppy"])
        cho_t = REGIME_TARGETS["choppy"]

        for name in (set(win_t.keys()) | set(cho_t.keys())):
            tgt = win_t.get(name, cho_t.get(name))
            tgt = win_t.get(name, cho_t.get(name))
            if blend_midpoint:
                wv = win_t.get(name)
                cv = cho_t.get(name)
                if isinstance(wv, (int, float)) and isinstance(cv, (int, float)):
                    tgt = 0.5 * (float(wv) + float(cv))
            cur = getattr(cfg, name, tgt)

            if name in BLEND_KNOBS and isinstance(tgt, (int, float)) and isinstance(cur, (int, float)):
                proposed = _apply_knob_blend(float(cur), float(tgt), alpha, name)
                lo, hi = _blend_clamp(name, winner, "choppy")
                if lo is not None:
                    proposed = max(lo, min(hi, proposed))

                # integers: confirm_candles and (for stability) per_product_cooldown_s
                if name in {"confirm_candles", "per_product_cooldown_s"}:
                    proposed = int(round(proposed))
                changes[name] = _apply_and_record(name, proposed)
            else:
                # Non-blend knobs follow the winner preset directly
                changes[name] = _apply_and_record(name, tgt)

    # ---- Offsets & telemetry (unchanged) ----
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
        "portfolio_vote": {k: int(v) for k, v in (portfolio_vote or {}).items() },
        "portfolio_regime": portfolio_regime,
        "vote_meta": meta,  # exposes hours/granularity/min_candles used
        "knob_changes": knob_changes,
        "global_changes": {k: f"{ov[0]}→{ov[1]}" for k, ov in changes.items()},
        "disabled_products": sorted(disabled),            # telemetry, shown by main if non-empty
        "disabled_details": {k: disabled_reasons[k] for k in sorted(disabled_reasons)},
        "offsets_changed": {k: offsets[k] for k in offsets},
    }
