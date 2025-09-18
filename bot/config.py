# bot/config.py
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

@dataclass
class BotConfig:
    # Products
    product_ids: List[str] = field(default_factory=lambda: [
        "BTC-USD", "ETH-USD", "XRP-USD", "ADA-USD",
        "ATOM-USD", "ALGO-USD", "XLM-USD", "HBAR-USD",
        "NEAR-USD", "SOL-USD", "DOGE-USD", "AVAX-USD",
        "LINK-USD", "SUI-USD", "LTC-USD", "CRO-USD",
        "DOT-USD", "ARB-USD",
    ])

    # EMA defaults / data requirements
    short_ema: int = 40
    long_ema: int = 120
    min_ticks: int = 120
    confirm_ticks: int = 2      # require 2 consecutive ticks to confirm a cross

    # Session controls
    dry_run: bool = False            
    usd_per_order: float = 20.0
    max_usd_per_day: float = 120.0
    cooldown_sec: int = 600
    hard_stop_bps: int | None = 200   # real emergency stop at ~2%        

    portfolio_id: Optional[str] = None
    log_level: int = logging.INFO

    # Maker/post-only settings
    maker_offset_bps: float = 5.0
    prefer_maker: bool = True                   # True by default. Offsets price at slightly cheaper value to lessen trading fees.
    prefer_maker_for_sells: bool = False         # If you want quick exits, set to False. Otherwise you can get stuck and miss out on a profit.
    
    maker_offset_bps_per_product: Dict[str, float] = field(default_factory=lambda: {
        "BTC-USD": 14.0,  "ETH-USD": 18.0,  "XRP-USD": 22.0,  "ADA-USD": 24.0,
        "ATOM-USD": 26.0, "ALGO-USD": 22.0, "XLM-USD": 24.0,  "HBAR-USD": 22.0,
        "NEAR-USD": 22.0, "SOL-USD": 16.0,  "DOGE-USD": 24.0, "AVAX-USD": 20.0,
        "LINK-USD": 20.0, "SUI-USD": 24.0,  "LTC-USD": 22.0,  "CRO-USD": 26.0,
        "DOT-USD": 18.0,  "ARB-USD": 20.0,
    })

    # EMA overrides per product (plus per-product min_ticks)
    ema_params_per_product: Dict[str, Dict[str, int]] = field(default_factory=lambda: {
         "BTC-USD":  {"short_ema": 45, "long_ema": 150, "min_ticks": 220},
         "ETH-USD":  {"short_ema": 45, "long_ema": 150, "min_ticks": 220},
         "SOL-USD":  {"short_ema": 40, "long_ema": 120, "min_ticks": 180},
         "LTC-USD":  {"short_ema": 40, "long_ema": 120, "min_ticks": 180},

         "XRP-USD":  {"short_ema": 30, "long_ema": 90,  "min_ticks": 120},
         "ADA-USD":  {"short_ema": 30, "long_ema": 90,  "min_ticks": 120},
         "ATOM-USD": {"short_ema": 30, "long_ema": 90,  "min_ticks": 120},
         "AVAX-USD": {"short_ema": 35, "long_ema": 100, "min_ticks": 120},
         "LINK-USD": {"short_ema": 35, "long_ema": 100, "min_ticks": 120},
         "DOGE-USD": {"short_ema": 30, "long_ema": 90,  "min_ticks": 120},
         "DOT-USD":  {"short_ema": 35, "long_ema": 100, "min_ticks": 120},  
         "ARB-USD":  {"short_ema": 35, "long_ema": 100, "min_ticks": 120},

         "NEAR-USD": {"short_ema": 28, "long_ema": 90,  "min_ticks": 120},
         "HBAR-USD": {"short_ema": 28, "long_ema": 90,  "min_ticks": 120},

         "SUI-USD":  {"short_ema": 20, "long_ema": 60,  "min_ticks": 90},
         "CRO-USD":  {"short_ema": 20, "long_ema": 60,  "min_ticks": 90},
         "ALGO-USD": {"short_ema": 30, "long_ema": 70,  "min_ticks": 80},
         "XLM-USD":  {"short_ema": 30, "long_ema": 70,  "min_ticks": 80},
    })

    # Advisors (RSI/MACD)
    enable_advisors: bool = True
    use_advisors: bool = True  # alias

    # RSI params
    rsi_period: int = 14
    rsi_buy_floor: float = 30.0        # oversold floor → blocks SELL if RSI < this
    rsi_sell_ceiling: float = 70.0     # overbought ceiling → blocks BUY if RSI > this

    # MACD params
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    macd_buy_min: float = +0.1       # allow buys if MACD >= this (bps)
    macd_sell_max: float = -0.1      # allow sells if MACD <= this (bps)

    # Other
    lookback_hours: int = 48
    processed_fills_max: int = 10000
    ema_deadband_bps: float = 6.0  # (0.06%) small band to avoid flapping

CONFIG = BotConfig()
