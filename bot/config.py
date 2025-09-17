# bot/config.py
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

@dataclass
class BotConfig:
    # Products
    product_ids: List[str] = (
        "BTC-USD",
        "ETH-USD",
        "XRP-USD",
        "ADA-USD",
        "ATOM-USD",
        "ALGO-USD",
        "XLM-USD",
        "HBAR-USD",
        "NEAR-USD",
        "SOL-USD",
        "DOGE-USD",
        "AVAX-USD",   # replaced MATIC
        "LINK-USD",
        "SUI-USD",
        "LTC-USD",
        "CRO-USD",
    )

    # EMA defaults / data requirements
    short_ema: int = 20
    long_ema: int = 50
    min_ticks: int = 60

    # Session controls
    dry_run: bool = True        # Change to False for live trading
    usd_per_order: float = 1.0
    max_usd_per_day: float = 20.0
    cooldown_sec: int = 300
    max_loss_bps: float = 100.0
    
    portfolio_id: Optional[str] = None
    log_level: int = logging.INFO

    # Maker/post-only settings
    maker_offset_bps: float = 5.0
    prefer_maker: bool = True
    maker_offset_bps_per_product: Dict[str, float] = field(default_factory=lambda: {
        "BTC-USD": 16.0,  "ETH-USD": 28.0,  "XRP-USD": 32.0,  "ADA-USD": 38.0,
        "ATOM-USD": 48.0, "ALGO-USD": 36.0, "XLM-USD": 38.0,  "HBAR-USD": 42.0,
        "NEAR-USD": 38.0, "SOL-USD": 32.0,  "DOGE-USD": 28.0, "AVAX-USD": 32.0,
        "LINK-USD": 44.0, "SUI-USD": 32.0,  "LTC-USD": 22.0,  "CRO-USD": 24.0,
    })

    # EMA overrides per product (plus per-product min_ticks)
    ema_params_per_product: Dict[str, Dict[str, int]] = field(default_factory=lambda: {
        "BTC-USD":  {"short_ema": 60, "long_ema": 200, "min_ticks": 200},
        "ETH-USD":  {"short_ema": 60, "long_ema": 200, "min_ticks": 200},
        "SOL-USD":  {"short_ema": 50, "long_ema": 150, "min_ticks": 150},
        "LTC-USD":  {"short_ema": 50, "long_ema": 150, "min_ticks": 150},

        "XRP-USD":  {"short_ema": 40, "long_ema": 120, "min_ticks": 120},
        "ADA-USD":  {"short_ema": 40, "long_ema": 100, "min_ticks": 120},
        "ATOM-USD": {"short_ema": 40, "long_ema": 90,  "min_ticks": 120},
        "AVAX-USD": {"short_ema": 40, "long_ema": 100, "min_ticks": 120},
        "LINK-USD": {"short_ema": 40, "long_ema": 80,  "min_ticks": 120},
        "DOGE-USD": {"short_ema": 40, "long_ema": 90,  "min_ticks": 120},

        "NEAR-USD": {"short_ema": 30, "long_ema": 100, "min_ticks": 100},
        "HBAR-USD": {"short_ema": 30, "long_ema": 100, "min_ticks": 100},

        "SUI-USD":  {"short_ema": 20, "long_ema": 60,  "min_ticks": 70},
        "CRO-USD":  {"short_ema": 20, "long_ema": 60,  "min_ticks": 70},
        "ALGO-USD": {"short_ema": 30, "long_ema": 70,  "min_ticks": 80},
        "XLM-USD":  {"short_ema": 30, "long_ema": 70,  "min_ticks": 90},
    })

    # Advisors (RSI/MACD). Keep both names for compatibility.
    use_advisors: bool = True
    enable_advisors: bool = True  # alias used by some versions

    # RSI params
    rsi_period: int = 14
    rsi_buy_floor = 30
    rsi_sell_ceiling = 70
    
    # MACD params
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    # Other
    lookback_hours: int = 48     # fills reconciliation window

# Single shared instance
CONFIG = BotConfig()
