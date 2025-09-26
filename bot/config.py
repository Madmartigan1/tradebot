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
        "DOT-USD","ARB-USD", "IP-USD", "WLFI-USD", "FLOKI-USD", "TOSHI-USD",
    ])

    # --- v1.0.3: Autotune (startup-only) ---
    autotune_enabled: bool = True
    autotune_preview_only: bool = True       # first time run set True: preview only (no changes applied)
    autotune_lookback_hours: int = 18        # bump to 24–72h if you skipped days

      
    # Session controls
    dry_run: bool = True        # Change to False for live trading            
    usd_per_order: float = 20.0
    max_usd_per_day: float = 120.0
    cooldown_sec: int = 600
    hard_stop_bps: int | None = 200   # real emergency stop at ~2%        

    # --- v1.0.3: Reconciliation during the session ---
    mid_reconcile_enabled: bool = True
    mid_reconcile_interval_minutes: int = 60  # hourly sweep
    reconcile_on_sell_attempt: bool = True    # quick sweep right before SELL



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
    dry_run: bool = True                # Change to False for live trading
    usd_per_order: float = 20.0
    daily_spend_cap_usd: float = 160.0  # buys stop after cap; sells continue
    per_product_cooldown_s: int = 900   
    hard_stop_bps: Optional[int] = 100  # emergency stop loss if asset drops below 1.0%

    # Maker/post-only
    prefer_maker: bool = True
    prefer_maker_for_sells: bool = True
    maker_offset_bps: float = 5.0

    maker_offset_bps_per_product: Dict[str, float] = field(default_factory=lambda: {
        # Tier A / very active — trimmed 2 bps
        "ETH-USD":16.0, "SOL-USD":18.0, "LINK-USD":18.0, "XRP-USD":20.0, "DOGE-USD":18.0, "LTC-USD":20.0,

        # Tier B — light trim where fills lagged; others unchanged
        "ADA-USD":20.0, "AVAX-USD":18.0, "DOT-USD":16.0, "ARB-USD":20.0, "FIL-USD":26.0, "NEAR-USD":20.0, "ATOM-USD":26.0,

        # Tier C / thinner or slower — mostly unchanged (small trims only where safe)
        "ALGO-USD":22.0, "XLM-USD":20.0, "CRO-USD":22.0, "SUI-USD":22.0, "HBAR-USD":20.0,

        # Other altcoins(EXPERIMENTAL)
        "IP-USD":22.0, "WLFI-USD":22.0, "FLOKI-USD":26.0, "TOSHI-USD":28.0,
    })


    # -------- The following block is not used. It was planned but decided to cancel long sitting unfilled orders manually on Coinbase. 
    # ---- Fill-speed / TTF targets + repricing (helpers) ----
    # Use these with your main loop:
    # On each candle close, if an order is still unfilled and the signal is valid,
    # reprice it (cancel & repost) up to max_reprices_per_signal times.
    #reprice_each_candle: bool = True               # reconsider resting makers every candle
    #reprice_if_unfilled_candles: int = 1           # reprice if still unfilled after this many candles
    #max_reprices_per_signal: int = 2               # don't spam cancels forever
    #reprice_jitter_ms: int = 1500                  # small random delay to avoid all-at-once cancels
    #-------------------------------------------------------------------------------------------------------------------------------------

    
    # ----- Autotune automatically sets these values based on market condidtions. Code below is irrelevant. ------------
    # Per-asset time-to-fill targets, in candles (matches 5m global candles)
    # Tier A (aim ≤1), Tier B (≤2), Tier C (≤3)
    #ttf_target_candles_per_product: Dict[str, int] = field(default_factory=lambda: {
        # Tier A
        #"ETH-USD":1, "SOL-USD":1, "LINK-USD":1, "LTC-USD":1, "XRP-USD":1, "DOGE-USD":1,
        # Tier B
        #"ADA-USD":2, "AVAX-USD":2, "DOT-USD":2, "ARB-USD":2, "FIL-USD":2, "NEAR-USD":2, "ATOM-USD":2,
        # Tier C
        #"ALGO-USD":3, "XLM-USD":3, "HBAR-USD":3, "CRO-USD":3, "SUI-USD":3, "IP-USD":3, "WLFI-USD":3,
    #})
    # --------------------------------------------------------------------------------------------------------------------

    # Disable per-coin EMA overrides for “global” behavior
    ema_params_per_product: Dict[str, Dict[str, int]] = field(default_factory=dict)

    # Misc
    lookback_hours: int = 48
    processed_fills_max: int = 10000
    ema_deadband_bps: float = 8.0
    log_level: int = logging.INFO
    portfolio_id: Optional[str] = None

CONFIG = BotConfig()
