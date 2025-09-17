import os
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from dotenv import load_dotenv
load_dotenv("APIkeys.env")

API_KEY = os.getenv("COINBASE_API_KEY")
API_SECRET = os.getenv("COINBASE_API_SECRET")
PORTFOLIO_ID = os.getenv("COINBASE_PORTFOLIO_ID") or None

if not API_KEY or not API_SECRET:
    raise SystemExit("ERROR: Missing COINBASE_API_KEY or COINBASE_API_SECRET in environment.")

@dataclass
class BotConfig:
    product_ids: List[str] = (
        "BTC-USD","ETH-USD","XRP-USD","ADA-USD","ATOM-USD","ALGO-USD","XLM-USD",
        "HBAR-USD","NEAR-USD","SOL-USD","DOGE-USD","AVAX-USD","LINK-USD",
        "SUI-USD","LTC-USD","CRO-USD",
    )

    # Global EMA defaults
    short_ema: int = 20
    long_ema: int = 50
    min_ticks: int = 60

    # Session controls
    dry_run: bool = True        # change to False for live trading
    usd_per_order: float = 1.0
    max_usd_per_day: float = 5.0
    cooldown_sec: int = 300
    portfolio_id: Optional[str] = PORTFOLIO_ID
    log_level: int = logging.INFO

    # Maker settings
    maker_offset_bps: float = 5.0
    prefer_maker: bool = True

    # Per-asset maker offsets (bps)
    maker_offset_bps_per_product: Dict[str, float] = field(default_factory=lambda: {
        "BTC-USD": 14.0, "ETH-USD": 18.0, "XRP-USD": 26.0, "ADA-USD": 32.0,
        "ATOM-USD": 38.0, "ALGO-USD": 28.0, "XLM-USD": 36.0, "HBAR-USD": 34.0,
        "NEAR-USD": 34.0, "SOL-USD": 28.0, "DOGE-USD": 28.0, "AVAX-USD": 32.0,
        "LINK-USD": 40.0, "SUI-USD": 30.0, "LTC-USD": 20.0, "CRO-USD": 24.0,
    })

    # Per-asset EMA overrides (and per-asset min_ticks)
    ema_params_per_product: Dict[str, Dict[str, int]] = field(default_factory=lambda: {
        # Big / liquid
        "BTC-USD": {"short_ema": 60, "long_ema": 200, "min_ticks": 200},
        "ETH-USD": {"short_ema": 60, "long_ema": 200, "min_ticks": 200},
        "SOL-USD": {"short_ema": 50, "long_ema": 150, "min_ticks": 150},
        "LTC-USD": {"short_ema": 50, "long_ema": 150, "min_ticks": 150},
        # Mid-tier
        "XRP-USD": {"short_ema": 40, "long_ema": 120, "min_ticks": 120},
        "ADA-USD": {"short_ema": 40, "long_ema": 100, "min_ticks": 120},
        "ATOM-USD":{"short_ema": 40, "long_ema": 90, "min_ticks": 120},
        "AVAX-USD":{"short_ema": 40, "long_ema": 100,"min_ticks": 120},
        "LINK-USD":{"short_ema": 40, "long_ema": 80, "min_ticks": 120},
        "DOGE-USD":{"short_ema": 40, "long_ema": 90, "min_ticks": 120},
        # Smaller majors
        "NEAR-USD":{"short_ema": 30, "long_ema": 100,"min_ticks": 100},
        "HBAR-USD":{"short_ema": 30, "long_ema": 100,"min_ticks": 100},
        # Less liquid
        "SUI-USD": {"short_ema": 20, "long_ema": 60, "min_ticks": 70},
        "CRO-USD": {"short_ema": 20, "long_ema": 60, "min_ticks": 70},
        "ALGO-USD":{"short_ema": 30, "long_ema": 70, "min_ticks": 80},
        "XLM-USD": {"short_ema": 30, "long_ema": 70, "min_ticks": 90},
    })
    
# Export a ready-to-use config instance
CONFIG = BotConfig()

