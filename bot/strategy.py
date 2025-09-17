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
    macd_buy_min_hist: float = -0.05
    macd_sell_max_hist: float =  0.05

def advisor_allows(side: str,
                   rsi_value: Optional[float],
                   macd_hist: Optional[float],
                   settings: AdvisorSettings) -> bool:
    """EMA is captain; advisors veto only if *clearly* bad."""
    s = side.upper()
    if s == "BUY":
        if settings.enable_rsi and rsi_value is not None:
            if not (settings.rsi_buy_min <= rsi_value <= settings.rsi_buy_max):
                return False
        if settings.enable_macd and macd_hist is not None:
            if macd_hist < settings.macd_buy_min_hist:
                return False
        return True
    else:  # SELL
        if settings.enable_rsi and rsi_value is not None:
            if not (settings.rsi_sell_min <= rsi_value <= settings.rsi_sell_max):
                return False
        if settings.enable_macd and macd_hist is not None:
            if macd_hist > settings.macd_sell_max_hist:
                return False
        return True
