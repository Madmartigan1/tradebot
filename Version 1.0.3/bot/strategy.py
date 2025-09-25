# bot/strategy.py
from dataclasses import dataclass
from typing import Optional

@dataclass
class AdvisorSettings:
    enable_rsi: bool = True
    rsi_period: int = 14
    rsi_buy_min: float = 25.0
    rsi_buy_max: float = 75.0
    rsi_sell_min: float = 25.0
    rsi_sell_max: float = 75.0

    enable_macd: bool = True
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    # Normalized option (bps of price); keep thresholds at 0 for sign-only behavior
    normalize_macd: bool = True
    macd_buy_min: float = 0.0    # bps if normalized, raw hist otherwise
    macd_sell_max: float = 0.0   # bps if normalized, raw hist otherwise

def _macd_metric(hist: Optional[float], price: Optional[float], normalize: bool) -> Optional[float]:
    if hist is None:
        return None
    if not normalize or price is None or price <= 0:
        return hist
    # Convert to basis points relative to price
    return 10_000.0 * (hist / price)

def advisor_allows(
    side: str,
    rsi_value: Optional[float],
    macd_hist: Optional[float],
    settings: AdvisorSettings,
    last_price: Optional[float] = None,
) -> bool:
    """
    EMA is captain; advisors veto only if *clearly* bad.

    RSI (one-sided veto):
      - BUY blocked only if RSI > rsi_buy_max (overbought)
      - SELL blocked only if RSI < rsi_sell_min (oversold)

    MACD:
      - Uses normalized histogram (bps) if normalize_macd=True.
      - BUY blocked if metric < macd_buy_min; SELL blocked if metric > macd_sell_max.
    """
    s = side.upper()

    # One-sided RSI veto
    if settings.enable_rsi and rsi_value is not None:
        if s == "BUY" and rsi_value > settings.rsi_buy_max:
            return False
        if s == "SELL" and rsi_value < settings.rsi_sell_min:
            return False

    # MACD veto
    if settings.enable_macd:
        m = _macd_metric(macd_hist, last_price, settings.normalize_macd)
        if m is not None:
            if s == "BUY" and m < settings.macd_buy_min:
                return False
            if s == "SELL" and m > settings.macd_sell_max:
                return False

    return True
