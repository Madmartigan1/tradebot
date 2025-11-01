# bot/orders.py v1.1.0
from typing import Tuple, Optional
import math

EPS = 1e-12

def round_down_to_inc(value: float, inc: float) -> float:
    if inc <= 0:
        return value
    steps = math.floor(value / inc + EPS)
    return steps * inc

def round_up_to_inc(value: float, inc: float) -> float:
    if inc <= 0:
        return value
    steps = math.ceil(value / inc - EPS)
    return steps * inc

def decimals_from_inc(inc: float) -> int:
    s = f"{inc:.10f}".rstrip("0").rstrip(".")
    return len(s.split(".")[1]) if "." in s else 0

def compute_maker_limit(
    coin_id: str, side: str, last_price: float, price_inc: float, base_inc: float,
    usd_per_order: float, offset_bps: float, bid: float | None = None, ask: float | None = None
) -> Tuple[float, float]:
    side = side.upper()
    offset = float(offset_bps) / 10_000.0

    if side == "BUY":
        ref = bid if (bid is not None and bid > 0) else last_price
        raw_price = ref * (1.0 - offset)
        limit_price = round_down_to_inc(raw_price, price_inc)
    else:
        ref = ask if (ask is not None and ask > 0) else last_price
        raw_price = ref * (1.0 + offset)
        limit_price = round_up_to_inc(raw_price, price_inc)

    base_size = 0.0
    if limit_price > 0:
        base_size = max(0.0, float(usd_per_order) / limit_price)
        base_size = round_down_to_inc(base_size, base_inc)

    # --- Post-only guard: ensure passive pricing vs live book ---
    price = limit_price
    if bid is not None and ask is not None and price_inc > 0:
        if side == "BUY":
            # Maker BUY must be <= best bid
            if price > bid:
                # snap best bid to tick using helper
                snapped = round_down_to_inc(bid, price_inc)
                price = min(snapped, bid)
        else:
            # Maker SELL must be >= best ask
            if price < ask:
                # snap best ask to tick using helper
                snapped = round_up_to_inc(ask, price_inc)
                price = max(snapped, ask)

    return price, base_size
    
