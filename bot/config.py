# ----v1.1.6----
# bot/config.py
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

def validate_config(cfg: "BotConfig"):
    """
    Coerce obvious misconfig values to safe ranges and emit warnings once.
    """
    import logging
    safe = True
    if getattr(cfg, "short_ema", 40) >= getattr(cfg, "long_ema", 120):
        logging.warning("Config: short_ema(%s) >= long_ema(%s); swapping to safe 40/120.",
                        getattr(cfg, "short_ema", None), getattr(cfg, "long_ema", None))
        cfg.short_ema, cfg.long_ema = 40, 120
        safe = False
    if not (1 <= getattr(cfg, "confirm_candles", 3) <= 5):
        logging.warning("Config: confirm_candles out of [1,5]; clamping.")
        cfg.confirm_candles = max(1, min(5, int(cfg.confirm_candles)))
        safe = False
    if not (0 < getattr(cfg, "ema_deadband_bps", 6.0) <= 20.0):
        logging.warning("Config: ema_deadband_bps out of (0,20]; clamping.")
        cfg.ema_deadband_bps = max(0.5, min(20.0, float(cfg.ema_deadband_bps)))
        safe = False
    # Maker reprice bounds
    if getattr(cfg, "maker_reprice_max", 1) < 0:
        cfg.maker_reprice_max = 0
        safe = False
    # REST soft limit sanity
    if getattr(cfg, "rest_rps_soft_limit", 8.0) <= 0:
        cfg.rest_rps_soft_limit = 8.0
        safe = False
    if getattr(cfg, "long_ema", 120) - getattr(cfg, "short_ema", 40) < 10:
        logging.warning("Config: short_ema(%s) and long_ema(%s) too close; enforcing minimum gap of 10 (using safe 40/120).",
                        getattr(cfg, "short_ema", None), getattr(cfg, "long_ema", None))
        cfg.short_ema, cfg.long_ema = 40, 120
        safe = False
    if not safe:
        logging.info("Config validation applied safe coercions.")
    return cfg

@dataclass
class BotConfig:
    # Coins (global settings apply to all)
    coin_ids: List[str] = field(default_factory=lambda: [
        "ETH-USD","XRP-USD","ADA-USD","TRAC-USD","ALGO-USD","XLM-USD","HBAR-USD",
        "NEAR-USD","SOL-USD","DOGE-USD","AVAX-USD","LINK-USD","SUI-USD","LTC-USD","CRO-USD",
        "DOT-USD","ARB-USD", "IP-USD", "FLOKI-USD", "PEPE-USD", "BONK-USD", 
        "SEI-USD", "SHIB-USD", "POL-USD",
    ])
    
    # Dry run used for paper trading. Set to False for live trading
    dry_run: bool = False
    # Max amount per trade and total trade amount per run
    usd_per_order: float = 10
    daily_spend_cap_usd: float = 120.0  # buys stop after cap; sells continue
    
    # -------- Candles v1.1.0 --------
    mode: str = "ws"                   # "ws" server side candle builds or "local" for local candle building
    candle_interval: str = "5m"        # "1m" | "5m" | "15m" ...
    min_candles: int = 120             # wait for indicator warm-up
    confirm_candles: int = 3           # consecutive cross confirms (3 for daytime, 2 for night)
    use_backfill: bool = True
    warmup_candles: int = 200

    # --- v1.0.3: Autotune (startup-only) ---
    autotune_enabled: bool = True
    autotune_preview_only: bool = False      # (optional)first time run set True: preview only (no changes applied)
    
    # ======================================================================================
    # How many hours of historical FILL data to sync during portfolio reconciliation.
    # - Used at: startup, periodic mid-session sweeps, and right-before SELL (guard).
    # - Affects ONLY portfolio/PNL/KPI backfill — NOT candles/indicators/AutoTune.
    # - Mid-session/on-demand reconcile is clamped to 6–168h for safety:
    #     effective_hours = min(48, max(6, lookback_hours))
    # - Startup reconcile is NOT clamped and will honor the full value.
    lookback_hours: int = 72
    # ======================================================================================
    
    # =====================================================================================
    # Regime vote lookback (used only for AutoTune’s regime voting; trading stays on 5m).
    # With vote interval = 15m, the effective vote window is:
    #   hours_used_for_vote = max(autotune_lookback_hours,
    #                             ceil(autotune_vote_min_candles * 15min))
    # We changed the detector’s minimum to require ≥120 candles (EMA long = 120),
    # so 36h ≈ 144×15m satisfies the requirement and yields non-choppy classifications.
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
    
    # Quartermaster exits
    enable_quartermaster: bool = False  # Set to True every now and then to maximize profits
    take_profit_bps: int = 1200         # 12%
    max_hold_hours: int = 36            # ⟵ was 24; now 36
    stagnation_close_bps: int = 200     # 2%
    flat_macd_abs_max: float = 0.40
    quartermaster_respect_macd: bool = True
    
    # --- v1.0.3: Reconciliation during the session ---
    mid_reconcile_enabled: bool = True
    mid_reconcile_interval_minutes: int = 90   # Sweep every [int]minutes
    reconcile_on_sell_attempt: bool = False    # disabled since it was causing connectivity issues

    # EMA (global)
    short_ema: int = 40                # good for 5m candles
    long_ema: int = 120

    # Advisors (RSI/MACD)
    enable_advisors: bool = True
    rsi_period: int = 14
    rsi_buy_max: float = 65.0          # BUY only if RSI ≤ 65
    rsi_sell_min: float = 35.0         # SELL only if RSI ≥ 35

    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    macd_buy_min: float = +2.0         # BUY only if MACD ≥ +2.0 bps
    macd_sell_max: float = -2.0        # SELL only if MACD ≤ −2.0 bps

    # Ops / Risk
    per_coin_cooldown_s: int = 600   # Wait time in seconds, per coin, before it trades again
    hard_stop_bps: Optional[int] = 300  # emergency stop loss if asset drops below 3.0%

    # Maker/post-only
    prefer_maker: bool = True
    prefer_maker_for_sells: bool = True
    maker_offset_bps: float = 10.0

    #Baseline BPS settings. Autotune adjusts these accordingly based on market microstructure(spread/volatility/adverse selection risk).
    maker_offset_bps_per_coin: Dict[str, float] = field(default_factory=lambda: {
        # Tier A / very active — trimmed 2 bps
        "ETH-USD":14.0, "SOL-USD":16.0, "LINK-USD":14.0, "XRP-USD":18.0, "DOGE-USD":20.0, "LTC-USD":18.0,

        # Tier B — light trim where fills lagged; others unchanged
        "ADA-USD":20.0, "AVAX-USD":18.0, "DOT-USD":16.0, "ARB-USD":20.0, "NEAR-USD":18.0, "TRAC-USD":18.0,

        # Tier C / thinner or slower — mostly unchanged (small trims only where safe)
        "ALGO-USD":20.0, "XLM-USD":18.0, "CRO-USD":22.0, "SUI-USD":22.0, "HBAR-USD":20.0, "POL-USD":22.0,

        # Other altcoins(EXPERIMENTAL)
        "IP-USD":22.0, "FLOKI-USD":24.0, "PEPE-USD":24.0, "BONK-USD":22.0, 
        "SEI-USD":24.0, "SHIB-USD":20.0,
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
    #ttf_target_candles_per_coin: Dict[str, int] = field(default_factory=lambda: {
        # Tier A
        #"ETH-USD":1, "SOL-USD":1, "LINK-USD":1, "LTC-USD":1, "XRP-USD":1, "DOGE-USD":1,
        # Tier B
        #"ADA-USD":2, "AVAX-USD":2, "DOT-USD":2, "ARB-USD":2, "FIL-USD":2, "NEAR-USD":2, "ATOM-USD":2,
        # Tier C
        #"ALGO-USD":3, "XLM-USD":3, "HBAR-USD":3, "CRO-USD":3, "SUI-USD":3, "IP-USD":3, "WLFI-USD":3,
    #})
    # --------------------------------------------------------------------------------------------------------------------

    # Disable per-coin EMA overrides for “global” behavior
    ema_params_per_coin: Dict[str, Dict[str, int]] = field(default_factory=dict)

    # Misc
    processed_fills_max: int = 10000
    ema_deadband_bps: float = 6.0
    log_level: int = logging.INFO
    portfolio_id: Optional[str] = None
    allow_position_fallback_for_avail = True
    full_exit_shave_increments: int = 1
    
    # --- WS reconnect/backoff knobs ---
    ws_reconnect_backoff_base: int = 5
    ws_reconnect_backoff_max: int = 60
    ws_reconnect_max_tries: int = 999999
    runloop_max_retries: int = 5

    # --- REST retry + pacing knobs ---
    rest_retry_attempts: int = 6
    rest_retry_backoff_min_ms: int = 600
    rest_retry_backoff_max_ms: int = 2400
    rest_retry_on_status: List[int] = field(default_factory=lambda: [429,500,502,503,504])
    rest_rps_soft_limit: float = 6.0
    live_balance_ttl_s: int = 20

    # --- Quartermaster dust guard knobs (already effective even if not exposed) ---
    qm_dust_suppress_minutes: int = 30
    qm_sell_buffer_mult: float = 1.0

    # --- WS liveness/keepalive ---
    ws_idle_warn_s: int = 60                 # warn if no WS msg in [int]s
    ws_idle_reconnect_s: int = 120           # force reconnect if idle >[int]s
    ws_resubscribe_interval_s: int = 1800    # reissue subscriptions every [int]s
    ws_ping_interval_s: int = 20             # best-effort ping cadence (if SDK supports it)
    ws_idle_flip_to_local_after: int = 3     # 0=disabled; else after N reconnects, switch to local candles
    
    stall_candle_factor = 2            # stalled if no close for 2 × granularity_sec
    stall_hard_reconnect_after = 3     # after 3 stall detections in a row, hard reconnect
    stall_flip_to_local_after = 3      # after 3 total stalls this session, switch to local candles
    stall_action_cooldown_s: int = 30
    stall_majority_flip_threshold: float = 0.5
    
    enable_rest_backstop = True          # turn on/off the backstop
    rest_backstop_idle_s = 45            # WS idle threshold to start polling
    rest_backstop_period_s = 10          # min seconds between REST polls
    rest_backstop_warmup = 2             # walk forward a couple of recent closes
    
    # Telemetry
    telemetry_heartbeat_s: int = 300   # 5 min; set 0 to disable

CONFIG = BotConfig()
