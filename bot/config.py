# ----v1.0.7----
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
        "DOT-USD","ARB-USD", "IP-USD", "WLFI-USD", "FLOKI-USD", "PEPE-USD",
    ])
    
    # Dry run used for paper trading. Set to False for live trading
    dry_run: bool = False         

    # --- v1.0.3: Autotune (startup-only) ---
    autotune_enabled: bool = True
    autotune_preview_only: bool = False      # (optional)first time run set True: preview only (no changes applied)
    
    # ======================================================================================
    # How many hours of historical FILL data to sync during portfolio reconciliation.
    # - Used at: startup, periodic mid-session sweeps, and right-before SELL (guard).
    # - Affects ONLY portfolio/PNL/KPI backfill — NOT candles/indicators/AutoTune.
    # - Mid-session/on-demand reconcile is clamped to 6–48h for safety:
    #     effective_hours = min(48, max(6, lookback_hours))
    # - Startup reconcile is NOT clamped and will honor the full value.
    lookback_hours: int = 48
    # ======================================================================================
    
    # =====================================================================================
    # Regime vote lookback (used only for AutoTune’s regime voting; trading stays on 5m).
    # With vote interval = 15m, the effective vote window is:
    #   hours_used_for_vote = max(autotune_lookback_hours,
    #                             ceil(autotune_vote_min_candles * 15min))
    # We changed the detector’s minimum to require ≥120 candles (EMA long = 120),
    # so 36h ≈ 144×15m satisfies the requirement and yields non-choppy classifications.
    # If you skip 2–3 days and want (not really necessary for a fresh start) the vote to span ~48–72h, temporarily bump this to 48–72.
    # (You can leave autotune_vote_min_candles at 144 to “lock” a 36h window, or raise it to force a larger window.)
    autotune_lookback_hours: int = 36         
    # =====================================================================================
    
    # --- Regime voting (decoupled from trading candles) ---
    # Use a dedicated timeframe for market regime voting (does NOT change trading candles)
    autotune_vote_interval: str = "15m"
    # Minimum number of vote candles required; at 15m, 144 candles ≈ 36 hours.
    # Effective voting window:
    #   hours_used_for_vote = max(autotune_lookback_hours,
    #                             ceil(autotune_vote_min_candles * interval_seconds / 3600))
    autotune_vote_min_candles: int = 144
    # ------------------------------------------------------
    
    # Elapsed re-tune lookback (fires hours after bot start; see main.py)
    elapsed_autotune_lookback_hours: int = 6
    
    # Quartermaster exits
    enable_quartermaster = True
    take_profit_bps = 600          # 6%
    max_hold_hours = 24            # ⟵ was 48; now 24h
    stagnation_close_bps = 200     # 2%
    flat_macd_abs_max = 0.40
    quartermaster_respect_macd = True
    
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
    rsi_buy_max: float = 65.0          # BUY only if RSI ≤ 60
    rsi_sell_min: float = 35.0         # SELL only if RSI ≥ 40

    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    macd_buy_min: float = +2.0         # BUY only if MACD ≥ +3.0 bps
    macd_sell_max: float = -2.0        # SELL only if MACD ≤ −3.0 bps

    # Ops / Risk
    usd_per_order: float = 30.0
    daily_spend_cap_usd: float = 240.0  # buys stop after cap; sells continue
    per_product_cooldown_s: int = 600   # Wait time in seconds, per coin, before it trades again
    hard_stop_bps: Optional[int] = 100  # emergency stop loss if asset drops below 1.0%

    # Maker/post-only
    prefer_maker: bool = True
    prefer_maker_for_sells: bool = True
    maker_offset_bps: float = 5.0

    maker_offset_bps_per_product: Dict[str, float] = field(default_factory=lambda: {
        # Tier A / very active — trimmed 2 bps
        "ETH-USD":16.0, "SOL-USD":18.0, "LINK-USD":18.0, "XRP-USD":20.0, "DOGE-USD":20.0, "LTC-USD":20.0,

        # Tier B — light trim where fills lagged; others unchanged
        "ADA-USD":20.0, "AVAX-USD":18.0, "DOT-USD":16.0, "ARB-USD":20.0, "FIL-USD":26.0, "NEAR-USD":20.0, "ATOM-USD":26.0,

        # Tier C / thinner or slower — mostly unchanged (small trims only where safe)
        "ALGO-USD":22.0, "XLM-USD":20.0, "CRO-USD":22.0, "SUI-USD":22.0, "HBAR-USD":20.0,

        # Other altcoins(EXPERIMENTAL)
        "IP-USD":22.0, "WLFI-USD":22.0, "FLOKI-USD":26.0, "PEPE-USD":28.0,  
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

    # --- Quartermaster exits (take-profit & time-in-trade) ---
    enable_quartermaster: bool = True

    # Take-profit band: sell if unrealized PnL ≥ this many bps (6% = 600 bps)
    take_profit_bps: int = 600

    # Time-in-trade “stagnation” exit:
    # If the position has been open ≥ this many hours AND |PnL| < stagnation_close_bps
    # AND MACD is ~flat, then close it to free capital.
    max_hold_hours: int = 24
    stagnation_close_bps: int = 200            # 2% band
    flat_macd_abs_max: float = 0.40            # “flat” MACD histogram threshold

    # Veto policy when quartermaster fires:
    #   - RSI veto is ignored (we’re taking profits or freeing capital, not chasing)
    #   - Optional MACD veto (keep it True to still avoid selling INTO strong momentum)
    quartermaster_respect_macd: bool = True

    # Disable per-coin EMA overrides for “global” behavior
    ema_params_per_product: Dict[str, Dict[str, int]] = field(default_factory=dict)

    # Misc
    processed_fills_max: int = 10000
    ema_deadband_bps: float = 6.0
    log_level: int = logging.INFO
    portfolio_id: Optional[str] = None

CONFIG = BotConfig()
