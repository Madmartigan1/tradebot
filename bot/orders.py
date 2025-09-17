from typing import Tuple

def round_down_to_inc(value: float, inc: float) -> float:
    if inc <= 0:
        return value
    return (int(value / inc)) * inc

def round_up_to_inc(value: float, inc: float) -> float:
    if inc <= 0:
        return value
    steps = int((value + 1e-15) / inc)
    return (steps if abs(steps * inc - value) < 1e-12 else steps + 1) * inc

def decimals_from_inc(inc: float) -> int:
    s = f"{inc:.10f}".rstrip("0").rstrip(".")
    return len(s.split(".")[1]) if "." in s else 0

def compute_maker_limit(
    product_id: str, side: str, last_price: float,
    price_inc: float, base_inc: float, usd_per_order: float, offset_bps: float
) -> Tuple[float, float]:
    offset = offset_bps / 10000.0
    if side == "BUY":
        raw_price = last_price * (1.0 - offset)
        limit_price = round_down_to_inc(raw_price, price_inc)
    else:
        raw_price = last_price * (1.0 + offset)
        limit_price = round_up_to_inc(raw_price, price_inc)
    base_size = max(0.0, usd_per_order / limit_price) if limit_price > 0 else 0.0
    base_size = round_down_to_inc(base_size, base_inc)
    return limit_price, base_size
