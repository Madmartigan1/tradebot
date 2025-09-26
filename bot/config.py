# bot/config.py
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

@dataclass
class BotConfig:
    # Products (global settings apply to all)
    product_ids: List[str] = field(default_factory=lambda: [
        "ETH-USD","XRP-USD","ADA-USD","ATOM-USD","ALGO-USD","XLM-USD","HBAR-USD", "FIL-USD",
        "NEAR-USD","SOL-USD","DOGE-USD","AVAX-USD","LINK-USD","SUI-USD","LTC-USD","CRO-USD",
        "DOT-USD","ARB-USD",
    ])

    # -------- Candles v1.0.2 --------
    mode: str = "ws"                   # or "local" if you want local aggregation, ws is fine by default.
    candle_interval: str = "5m"        # "1m" | "5m" | "15m" ...
    min_candles: int = 120             # wait for indicator warm-up
    confirm_candles: int = 3           # consecutive cross confirms
    use_backfill: bool = True
    warmup_candles: int = 200

    # EMA (global)
    short_ema: int = 40                # good for 5m candles
    long_ema: int = 120

    # Advisors (RSI/MACD)
    enable_advisors: bool = True
    rsi_period: int = 14
    rsi_buy_max: float = 60.0          # BUY only if RSI ≤ 60
    rsi_sell_min: float = 40.0         # SELL only if RSI ≥ 40

    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    macd_buy_min: float = +3.0         # BUY only if MACD ≥ +3.0 bps
    macd_sell_max: float = -3.0        # SELL only if MACD ≤ −3.0 bps

    # Ops / Risk
    dry_run: bool = True               # Change to False for live trading
    usd_per_order: float = 20.0
    daily_spend_cap_usd: float = 160.0  # buys stop after cap; sells continue
    per_product_cooldown_s: int = 900
    hard_stop_bps: Optional[int] = 120  # emergency stop loss if asset drops below 1.2%

    # Maker/post-only
    prefer_maker: bool = True
    prefer_maker_for_sells: bool = False
    maker_offset_bps: float = 5.0

    maker_offset_bps_per_product: Dict[str, float] = field(default_factory=lambda: {
        "ETH-USD":18.0,"XRP-USD":22.0,"ADA-USD":22.0,"ATOM-USD":26.0,"ALGO-USD":22.0, "FIL-USD":26.0,
        "XLM-USD":22.0,"HBAR-USD":20.0,"NEAR-USD":20.0,"SOL-USD":20.0,"DOGE-USD":20.0,"AVAX-USD":20.0,
        "LINK-USD":20.0,"SUI-USD":22.0,"LTC-USD":22.0,"CRO-USD":22.0,"DOT-USD":18.0,"ARB-USD":22.0,
    })

    # ---- Fill-speed / TTF targets + repricing (helpers) ----
    # Use these with your main loop:
    # On each candle close, if an order is still unfilled and the signal is valid,
    # reprice it (cancel & repost) up to max_reprices_per_signal times.
    reprice_each_candle: bool = True               # reconsider resting makers every candle
    reprice_if_unfilled_candles: int = 1           # reprice if still unfilled after this many candles
    max_reprices_per_signal: int = 2               # don't spam cancels forever
    reprice_jitter_ms: int = 1500                  # small random delay to avoid all-at-once cancels

    # Per-asset time-to-fill targets, in candles (matches 5m global candles)
    # Tier A (aim ≤1), Tier B (≤2), Tier C (≤3)
    ttf_target_candles_per_product: Dict[str, int] = field(default_factory=lambda: {
        # Tier A
        "ETH-USD":1, "SOL-USD":1, "LINK-USD":1, "LTC-USD":1, "XRP-USD":1, "DOGE-USD":1,
        # Tier B
        "ADA-USD":2, "AVAX-USD":2, "DOT-USD":2, "ARB-USD":2, "FIL-USD":2, "NEAR-USD":2, "ATOM-USD":2,
        # Tier C
        "ALGO-USD":3, "XLM-USD":3, "HBAR-USD":3, "CRO-USD":3, "SUI-USD":3,
    })

    # Disable per-coin EMA overrides for “global” behavior
    ema_params_per_product: Dict[str, Dict[str, int]] = field(default_factory=dict)

    # Misc
    lookback_hours: int = 48
    processed_fills_max: int = 10000
    ema_deadband_bps: float = 8.0
    log_level: int = logging.INFO
    portfolio_id: Optional[str] = None

CONFIG = BotConfig()
