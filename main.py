# main.py (v1.1.4 — APIkeys.env like v1.0.4; hybrid AutoTune; Windows-friendly Ctrl+C; telemetry with detail added)
import os
import sys
import time
import threading
import logging
import signal
import argparse

from textwrap import dedent
from typing import Optional
from dotenv import load_dotenv

from bot.config import CONFIG, validate_config
from bot.autotune import autotune_config
from bot.tradebot import TradeBot

# Commented out to keep clearer formatting
#if os.name == "nt":
#   try:
#        sys.stdout.reconfigure(encoding="utf-8")
#        sys.stderr.reconfigure(encoding="utf-8")
#    except Exception:
#        pass


# Optional elapsed-time AutoTune refresh (one-shot after N hours)
AUTOTUNE_ELAPSED_REFRESH_ENABLED = True
AUTOTUNE_ELAPSED_REFRESH_HOURS = 3

_shutdown_once = threading.Event()
_run_start_monotonic = time.monotonic()

def _str2bool(v: str) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("1","true","t","yes","y","on"):
        return True
    if s in ("0","false","f","no","n","off"):
        return False
    raise argparse.ArgumentTypeError(f"Expected boolean, got {v!r}")
    
def _parse_coins_delta(s: str) -> dict[str, list[str]]:
    """
    Parse a comma list with *sticky* operators:
      - A leading '=' means full replacement: '=A,B,C'
      - '+' or '-' sets the current mode and applies to subsequent bare tokens
        until another operator appears:
          '-DOGE-USD,PEPE-USD,+TAO-USD,ZEC-USD' → remove DOGE & PEPE; add TAO & ZEC
      - If no operator has been set yet, bare tokens default to +add.
    Returns {'add':[], 'remove':[], 'replace':[]}.
    """
    adds, removes, repl = [], [], []
    txt = (s or "").strip()
    if not txt:
        return {"add": adds, "remove": removes, "replace": repl}

    # Explicit full replacement if the entire arg starts with '='
    if txt.startswith("="):
        body = txt[1:]
        repl = [t.strip().upper() for t in body.split(",") if t.strip()]
        return {"add": [], "remove": [], "replace": repl}

    # Sticky +/- mode across tokens until changed
    mode = "add"  # default until we see an operator
    for raw in txt.split(","):
        tok = raw.strip()
        if not tok:
            continue
        op = tok[0]
        if op in {"+", "-"}:
            tok = tok[1:].strip()
            if not tok:
                continue
            mode = "add" if op == "+" else "remove"
        sym = tok.upper()
        if mode == "remove":
            removes.append(sym)
        else:
            adds.append(sym)
    return {"add": adds, "remove": removes, "replace": repl}

# --- pretty help formatter that preserves newlines without overrides ---
class TradeBotHelp(argparse.RawTextHelpFormatter):
    def __init__(self, prog):
        # Wider second column and overall width; RawText keeps your \n formatting
        super().__init__(prog, max_help_position=70, width=160)
        
# Boolean flags as explicit true/false (no --no-* variants)
_BOOL_KW = {"type": _str2bool, "nargs": "?", "const": True, "metavar": "1|0"}

def parse_cli_overrides(argv=None):
    # Build a Defaults section from current CONFIG so help is truthful without cluttering each flag line
    defaults_lines = [
        "Defaults:",
        f"  dry_run: {CONFIG.dry_run}",
        f"  usd_per_order: {CONFIG.usd_per_order}",
        f"  daily_spend_cap_usd: {CONFIG.daily_spend_cap_usd}",
        f"  mode: {getattr(CONFIG, 'mode', 'local')}",
        f"  candle_interval: {getattr(CONFIG, 'candle_interval', '5m')}",
        f"  confirm_candles: {getattr(CONFIG, 'confirm_candles', 3)}",
        f"  ema_deadband_bps: {getattr(CONFIG, 'ema_deadband_bps', 6.0)}",
        f"  short_ema: {getattr(CONFIG, 'short_ema', 40)}",
        f"  long_ema: {getattr(CONFIG, 'long_ema', 120)}",
        f"  rsi_period: {getattr(CONFIG, 'rsi_period', 14)}",
        f"  rsi_buy_max: {getattr(CONFIG, 'rsi_buy_max', 65.0)}",
        f"  rsi_sell_min: {getattr(CONFIG, 'rsi_sell_min', 35.0)}",
        f"  macd_fast: {getattr(CONFIG, 'macd_fast', 12)}",
        f"  macd_slow: {getattr(CONFIG, 'macd_slow', 26)}",
        f"  macd_signal: {getattr(CONFIG, 'macd_signal', 9)}",
        f"  macd_buy_min: {getattr(CONFIG, 'macd_buy_min', 2.0)}",
        f"  macd_sell_max: {getattr(CONFIG, 'macd_sell_max', -2.0)}",
        f"  autotune_enabled: {getattr(CONFIG, 'autotune_enabled', True)}",
        f"  lookback_hours: {getattr(CONFIG, 'lookback_hours', 72)}",
        f"  enable_quartermaster: {getattr(CONFIG, 'enable_quartermaster', False)}",
        f"  take_profit_bps: {getattr(CONFIG, 'take_profit_bps', 1200)}",
        f"  max_hold_hours: {getattr(CONFIG, 'max_hold_hours', 36)}",
        f"  stagnation_close_bps: {getattr(CONFIG, 'stagnation_close_bps', 200)}",
        f"  mid_reconcile_enabled: {getattr(CONFIG, 'mid_reconcile_enabled', True)}",
        f"  mid_reconcile_interval_minutes: {getattr(CONFIG, 'mid_reconcile_interval_minutes', 90)}",
        f"  enable_advisors: {getattr(CONFIG, 'enable_advisors', True)}",
        f"  per_coin_cooldown_s: {getattr(CONFIG, 'per_coin_cooldown_s', 600)}",
        f"  hard_stop_bps: {getattr(CONFIG, 'hard_stop_bps', 300)}",
        f"  prefer_maker: {getattr(CONFIG, 'prefer_maker', True)}",
        f"  prefer_maker_for_sells: {getattr(CONFIG, 'prefer_maker_for_sells', True)}",
        f"  maker_offset_bps: {getattr(CONFIG, 'maker_offset_bps', 10.0)}",
        "",
        "  Default coin pairs:",
        """\
        ┌──────────┬──────────┬──────────┐
        │ ETH-USD  │ XRP-USD  │ ADA-USD  │
        │ TRAC-USD │ ALGO-USD │ XLM-USD  │
        │ HBAR-USD │ NEAR-USD │ SOL-USD  │
        │ DOGE-USD │ AVAX-USD │ LINK-USD │
        │ SUI-USD  │ LTC-USD  │ CRO-USD  │
        │ DOT-USD  │ ARB-USD  │ IP-USD   │
        │ FLOKI-USD│ PEPE-USD │ BONK-USD │
        │ SEI-USD  │ SHIB-USD │ POL-USD  │
        └──────────┴──────────┴──────────┘
        """, 
    ]
    epilog_text = "\n".join(defaults_lines)
    
    autotune_overrides = [
        "",
        "AutoTune may override at runtime:",
        "  • confirm_candles",
        "  • per_coin_cooldown_s",
        "  • rsi_buy_max, rsi_sell_min",
        "  • macd_buy_min, macd_sell_max",
        "  • ema_deadband_bps",
        "  • maker_offset_bps_per_coin (per-asset offsets)",
        "",
        "AutoTune does NOT change:",
        "  • candle_interval, mode (ws/local)",
        "  • short_ema, long_ema (EMA lengths)",
        "  • usd_per_order, daily_spend_cap_usd",
        "  • prefer_maker / prefer_maker_for_sells",
        "  • stop-loss (hard_stop_bps)",
        "  • Coin list (beyond advisory telemetry notes)",
        "",
        "Tip: To force your own parameters, disable AutoTune, e.g.:",
        "  python main.py --enable-autotune=0 --confirm-candles=1 --cooldown-time=200 --deadband=4 --short-ema=50 --long-ema=110",
    ]
    epilog_text = "\n".join(defaults_lines + autotune_overrides)
    
    p = argparse.ArgumentParser(
        add_help=True,
        description="Tradebot — adaptive Coinbase trading bot",
        formatter_class=TradeBotHelp,
        epilog=epilog_text,
    )
    # Organize flags into readable groups
    g_run = p.add_argument_group("Runtime overrides")
    g_limits = p.add_argument_group("Limits")
    g_coins = p.add_argument_group("Coin selection")
    g_candles = p.add_argument_group("Candles and lookback")
    g_orders = p.add_argument_group("Order placement")
    g_qm = p.add_argument_group("Quartermaster exits")
    g_risk = p.add_argument_group("Risk & pacing")
    g_maker = p.add_argument_group("Maker settings")
    g_ind = p.add_argument_group("Indicators (EMA/RSI/MACD)")
    
    # booleans (BooleanOptionalAction removes noisy [METAVAR] and adds [bool])
    g_run.add_argument(
        "--dry-run",
        dest="dry_run",
        **_BOOL_KW,
        help="Paper trade without sending live orders.",
    )
    g_run.add_argument(
        "--enable-quartermaster",
        dest="enable_quartermaster",
        **_BOOL_KW,
        help="Enable or disable Quartermaster depletion logic.",
    )
    g_run.add_argument(
        "--enable-autotune",
        dest="autotune_enabled",
        **_BOOL_KW,
        help="Enable or disable AutoTune at startup (and the optional elapsed refresh).",
    )
    g_run.add_argument(
        "--mid-session-reconcile",
        dest="mid_reconcile_enabled",
        **_BOOL_KW,
        help="Enable or disable the mid-session reconcile scheduler.",
    )
    g_run.add_argument(
        "--enable-advisors",
        dest="enable_advisors",
        **_BOOL_KW,
        help="Enable or disable RSI/MACD advisors.",
    )
    
    # money / limits
    g_limits.add_argument(
        "--usd-per-order",
        dest="usd_per_order",
        type=float,
        metavar="USD",
        help="Maximum USD per buy order.",
    )
    g_limits.add_argument(
        "--max-spend-cap",
        dest="daily_spend_cap_usd",
        type=float,
        metavar="USD",
        help="Daily USD spend cap for buys (sells continue).",
    )
    
    # candles & lookback
    g_candles.add_argument(
        "--candle-mode",
        dest="mode",
        type=lambda s: s.strip().lower(),
        choices=["ws", "local"],
        metavar="ws|local",
        help="Select candle builder: 'ws' (server candles) or 'local' (client-side candles).",
    )
    g_candles.add_argument(
        "--candle-interval",
        dest="candle_interval",
        type=str,
        metavar="1m|5m|15m",
        help="Candle interval for trading indicators (e.g., 1m, 5m, 15m).",
    )
    g_candles.add_argument(
        "--confirm-candles",
        dest="confirm_candles",
        type=int,
        metavar="N",
        help="Consecutive cross confirmations (1–5).",
    )
    g_candles.add_argument(
        "--lookback-hours",
        dest="lookback_hours",
        type=int,
        metavar="HOURS",
        help="Historical fills lookback window used by reconcile/startup (hours).",
    )
    g_candles.add_argument(
        "--mid-reconcile-interval",
        dest="mid_reconcile_interval_minutes",
        type=int,
        metavar="MIN",
        help="Mid-session reconcile sweep cadence (minutes).",
    )

    # quartermaster exits
    g_qm.add_argument(
        "--quartermaster-profit",
        dest="take_profit_bps",
        type=int,
        metavar="BPS",
        help="Quartermaster take-profit target in basis points (e.g., 1200 = 12%%).",
    )
    g_qm.add_argument(
        "--quartermaster-hold-time",
        dest="max_hold_hours",
        type=int,
        metavar="HOURS",
        help="Quartermaster max hold time before exit (hours).",
    )
    g_qm.add_argument(
        "--quartermaster-stagnation-exit",
        dest="stagnation_close_bps",
        type=int,
        metavar="BPS",
        help="Quartermaster stagnation exit threshold in basis points.",
    )

    # risk & pacing
    g_risk.add_argument(
        "--cooldown-time",
        dest="per_coin_cooldown_s",
        type=int,
        metavar="SEC",
        help="Per-coin cooldown between signals (seconds).",
    )
    g_risk.add_argument(
        "--stop-loss",
        dest="hard_stop_bps",
        type=int,
        metavar="BPS",
        help="Emergency stop loss in basis points (set to 0 to effectively disable).",
    )
    g_risk.add_argument(
        "--deadband",
        dest="ema_deadband_bps",
        type=float,
        metavar="BPS",
        help="EMA crossover deadband in basis points (reduces chop).",
    )
    
    # indicators: EMA / RSI / MACD
    g_ind.add_argument(
        "--short-ema",
        dest="short_ema",
        type=int,
        metavar="N",
        help="Short EMA period (must be < long EMA; validator will swap to 40/120 if unsafe).",
    )
    g_ind.add_argument(
        "--long-ema",
        dest="long_ema",
        type=int,
        metavar="N",
        help="Long EMA period (must be > short EMA; validator will swap to 40/120 if unsafe).",
    )
    g_ind.add_argument(
        "--rsi-period",
        dest="rsi_period",
        type=int,
        metavar="N",
        help="RSI lookback period.",
    )
    g_ind.add_argument(
        "--rsi-max",
        dest="rsi_buy_max",
        type=float,
        metavar="VAL",
        help="BUY only if RSI ≤ VAL.",
    )
    g_ind.add_argument(
        "--rsi-min",
        dest="rsi_sell_min",
        type=float,
        metavar="VAL",
        help="SELL only if RSI ≥ VAL.",
    )
    g_ind.add_argument(
        "--macd-fast",
        dest="macd_fast",
        type=int,
        metavar="N",
        help="MACD fast EMA length.",
    )
    g_ind.add_argument(
        "--macd-slow",
        dest="macd_slow",
        type=int,
        metavar="N",
        help="MACD slow EMA length.",
    )
    g_ind.add_argument(
        "--macd-signal",
        dest="macd_signal",
        type=int,
        metavar="N",
        help="MACD signal EMA length.",
    )
    g_ind.add_argument(
        "--macd-min",
        dest="macd_buy_min",
        type=float,
        metavar="BPS",
        help="BUY only if MACD ≥ BPS (basis points).",
    )
    g_ind.add_argument(
        "--macd-max",
        dest="macd_sell_max",
        type=float,
        metavar="BPS",
        help="SELL only if MACD ≤ BPS (basis points).",
    )
    
    # maker settings
    g_maker.add_argument(
        "--maker-offset",
        dest="maker_offset_bps",
        type=float,
        metavar="BPS",
        help="Default maker offset in basis points (per-coin AutoTune may override).",
    )
    
    # order placement preferences
    g_orders.add_argument(
        "--prefer-maker",
        dest="prefer_maker",
        **_BOOL_KW,
        help="Prefer post-only maker orders when possible.",
    )
    g_orders.add_argument(
        "--prefer-maker-for-sells",
        dest="prefer_maker_for_sells",
        **_BOOL_KW,
        help="Apply post-only preference to sell/exit orders as well.",
    )

    # coins (multiline example kept tidy with explicit newlines)
    g_coins.add_argument(
        "--coins",
        dest="coins",
        action="append",          # allow multiple occurrences
        type=str,
        metavar="LIST",
        help=(
            "Add/remove/replace coins. Operators are *sticky* until changed:\n"
            "  add      :  '+SOL-USD,+AVAX-USD,+ALGO-USD'\n"
            "  remove   :  '-DOGE-USD,-PEPE-USD'\n"
            "  mixed    :  '-BONK-USD,FLOKI-USD,+TAO-USD,ZEC-USD,TRUMP-USD'\n"
            "                 (removes BONK & FLOKI; adds TAO, ZEC, TRUMP)\n"
            "  replace  :  '=BTC-USD,ETH-USD'  (leading '=' replaces the whole list)"
        ),
    )
    
    return p.parse_known_args(argv)[0]
    
def _finalize_and_exit(code: int = 0):
    try:
        logging.shutdown()
    finally:
        os._exit(code)

def _normalize_log_level(val):
    default = logging.INFO
    if isinstance(val, int):
        return val
    if isinstance(val, str):
        s = val.strip()
        if s.isdigit():
            try:
                return int(s)
            except Exception:
                return default
        return getattr(logging, s.upper(), default)
    return default

def _setup_logging():
    lvl = getattr(CONFIG, "log_level", "INFO")
    level_int = _normalize_log_level(lvl)
    logging.basicConfig(
        level=level_int,
        format="%(asctime)s | %(levelname)s | %(threadName)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logging.getLogger("urllib3").setLevel(max(level_int, logging.WARNING))
    logging.getLogger("websocket").setLevel(max(level_int, logging.WARNING))
    return logging.getLogger("tradebot")

def _load_keys_from_envfile():
    """
    v1.0.4 behavior:
      - Load from APIkeys.env (or ENV_PATH override)
      - Expect COINBASE_API_KEY / COINBASE_API_SECRET / PORTFOLIO_ID
      - No key sanitization; supports HMAC or PEM/JWT depending on what's in the env file.
    """
    env_path = os.getenv("ENV_PATH", "APIkeys.env")
    load_dotenv(dotenv_path=env_path, override=False)

    api_key = os.getenv("COINBASE_API_KEY")
    api_secret = os.getenv("COINBASE_API_SECRET")
    portfolio_id = os.getenv("PORTFOLIO_ID") or getattr(CONFIG, "portfolio_id", None)

    if not api_key or not api_secret:
        logging.getLogger("tradebot").error(
            "Missing COINBASE_API_KEY / COINBASE_API_SECRET in environment "
            f"(loaded from {env_path})."
        )
        _finalize_and_exit(1)

    return api_key, api_secret, portfolio_id

def _elapsed_autotune_once_with_bot(
    bot: TradeBot,
    api_key: str,
    api_secret: str,
    portfolio_id: Optional[str],
):
    """
    One-shot AutoTune run after AUTOTUNE_ELAPSED_REFRESH_HOURS, reusing the
    existing authenticated REST client from the running TradeBot instance.
    """
    logger = logging.getLogger("autotune-elapsed")

    if not getattr(CONFIG, "autotune_enabled", False):
        return

    # Wait until the elapsed window passes (but allow clean shutdown)
    target_sec = int(AUTOTUNE_ELAPSED_REFRESH_HOURS * 3600)
    step = 5
    while not _shutdown_once.is_set():
        if (time.monotonic() - _run_start_monotonic) >= target_sec:
            break
        time.sleep(step)

    # If shutdown was requested during (or just after) the wait window, bail out cleanly.
    if _shutdown_once.is_set() or getattr(bot, "stop_requested", False):
        try:
            logger.info("Skipped AUTOTUNE(elapsed): Exiting bot...")
        except Exception:
            pass
        return

    try:
        logger.info("AUTOTUNE (elapsed): starting one-shot update…")
        summary = autotune_config(
            CONFIG,
            api_key=api_key or "",
            api_secret=api_secret or "",
            portfolio_id=portfolio_id,
            rest=getattr(bot, "rest", None),  # reuse the running bot's client
            preview_only=getattr(CONFIG, "autotune_preview_only", True),
        )
        # Keep AutoTune results scoped to currently active coins
        active = set(getattr(CONFIG, "coin_ids", []))
        offsets = {k: v for k, v in (summary.get("offsets_changed") or {}).items() if k in active}
        summary["offsets_changed"] = offsets
        disabled = [p for p in (summary.get("disabled_coins") or []) if p in active]
        summary["disabled_coins"] = disabled
        logger.info(
            "AUTOTUNE(elapsed %dh): mode=%s | regime=%s | winner=%s | share=%.2f | alpha=%.2f",
            AUTOTUNE_ELAPSED_REFRESH_HOURS,
            summary.get("mode"),
            summary.get("portfolio_regime"),
            summary.get("winner"),
            float(summary.get("share") or 0.0),
            float(summary.get("alpha") or 0.0),
        )
        logger.info("AUTOTUNE(elapsed) votes: %s", summary.get("portfolio_vote"))
        logger.info("AUTOTUNE(elapsed) knob changes: %s", summary.get("global_changes"))

        # Offsets: guard + show only active coins
        active = set(getattr(CONFIG, "coin_ids", []))
        offsets_all = summary.get("offsets_changed") or summary.get("offsets") or {}
        if isinstance(offsets_all, dict):
            offsets_active = {k: v for k, v in offsets_all.items() if k in active}
            if offsets_active:
                logger.info("AUTOTUNE(elapsed) offsets (active only): %s", offsets_active)
            else:
                logger.info("AUTOTUNE(elapsed) offsets: (none for active set)")
        else:
            logger.info("AUTOTUNE(elapsed) offsets: (not provided)")

        cands = (summary.get("disabled_coins") or []) if isinstance(summary, dict) else []
        if cands:
            logger.info("AUTOTUNE(elapsed, advisory only) would disable: %s", cands)
        logger.info("AUTOTUNE (elapsed): complete.")
    except Exception as e:
        logger.exception("AUTOTUNE (elapsed) failed: %s", e)

def _request_shutdown(bot: TradeBot | None, code: int = 0):
    if not _shutdown_once.is_set():
        _shutdown_once.set()
        try:
            if bot is not None:
                bot.stop_requested = True
        except Exception:
            pass
        try:
            if bot is not None and hasattr(bot, "close"):
                bot.close()
        except Exception:
            pass
    _finalize_and_exit(code)


def main():
    log = _setup_logging()
    
    # Apply CLI overrides before touching CONFIG anywhere else
    args = parse_cli_overrides(sys.argv[1:])
    overrides = {}
    
    # Handle --coins specially (supports single flag with mixed +/- and bare tokens)
    if getattr(args, "coins", None):
        # Merge deltas from all provided --coins flags (works with one or many)
        merged = {"add": [], "remove": [], "replace": []}
        for chunk in args.coins:
            d = _parse_coins_delta(chunk)
            merged["add"].extend(d["add"])
            merged["remove"].extend(d["remove"])
            merged["replace"].extend(d["replace"])

        # Start from explicit replacement base (if any bare tokens were supplied),
        # otherwise from current CONFIG defaults.
        current = [s.strip().upper() for s in getattr(CONFIG, "coin_ids", [])]
        if merged["replace"]:
            new_list = list(dict.fromkeys(merged["replace"]))  # de-dupe, preserve order
        else:
            new_list = list(current)

        # Apply removals
        if merged["remove"]:
            to_remove = set(merged["remove"])
            new_list = [c for c in new_list if c not in to_remove]

        # Apply additions (append in order; skip duplicates)
        for c in merged["add"]:
            if c not in new_list:
                new_list.append(c)

        if new_list:
            CONFIG.coin_ids = new_list
            overrides["coin_ids"] = new_list
        else:
            log.warning("After applying --coins, coin list is empty; keeping default list.")
        
    for k, v in vars(args).items():
        if k == "coins":
            continue
        if v is not None:
            setattr(CONFIG, k, v)
            overrides[k] = v
    
    if overrides:
        log.info("CLI overrides applied: %s", {k: overrides[k] for k in sorted(overrides)})
    
    # Validate + coerce the global CONFIG in place
    validate_config(CONFIG)
    
    # --- v1.0.4 key loading (from APIkeys.env) ---
    api_key, api_secret, portfolio_id = _load_keys_from_envfile()

    # Construct the bot (TradeBot builds REST+WS client using api_key/api_secret)
    try:
        bot = TradeBot(CONFIG, api_key=api_key, api_secret=api_secret, portfolio_id=portfolio_id)
    except Exception as e:
        logging.getLogger("tradebot").exception("Failed to construct TradeBot: %s", e)
        _request_shutdown(None, 1)
        
    # Log & exit on any uncaught exceptions (main thread)
    def _sys_excepthook(exc_type, exc, tb):
        if exc_type is KeyboardInterrupt:
            return
        logging.getLogger("tradebot").exception("Uncaught exception: %s", exc)
        _request_shutdown(bot, 1)
    sys.excepthook = _sys_excepthook

    # Same for background threads (Python 3.8+)
    if hasattr(threading, "excepthook"):
        def _thread_excepthook(args):
            if isinstance(args.exc_value, KeyboardInterrupt):
                return
            logging.getLogger("tradebot").exception(
                "Uncaught exception in thread %s: %s", args.thread.name, args.exc_value
            )
            _request_shutdown(bot, 1)
        threading.excepthook = _thread_excepthook
    
    # POSIX signals (after bot exists so we can shut it down cleanly).
    # On Windows, we rely on KeyboardInterrupt below.
    if os.name != "nt":
        def _sigterm(_signo, _frame):
            if _shutdown_once.is_set(): return
            _request_shutdown(bot, 0)
        signal.signal(signal.SIGINT, _sigterm)
        signal.signal(signal.SIGTERM, _sigterm)
    else:
        # Optional: treat Ctrl+Break like Ctrl+C on Windows
        if hasattr(signal, "SIGBREAK"):
            def _sigbreak(_signo, _frame):
                if _shutdown_once.is_set(): return
                logging.getLogger("tradebot").info("Ctrl+Break received; shutting down...")
                _request_shutdown(bot, 0)
            signal.signal(signal.SIGBREAK, _sigbreak)

    # Expose the authenticated REST client so autotune.py reuses the same client for the 18h lookback
    setattr(CONFIG, "_rest", getattr(bot, "rest", None))

    # --- v1.0.7 startup order: Reconcile → AutoTune → Open WS ---
    # 1) Startup reconcile FIRST so KPI (.state/trades.csv) is available to AutoTune
    try:
        lookback = int(getattr(CONFIG, "lookback_hours", 48))
        log.info("Gathering trade data from past %s hours...", lookback)
        bot.reconcile_recent_fills(lookback)
    except Exception as e:
        log.warning("Startup reconcile failed: %s", e)
    try:
        bot.set_run_baseline()
    except Exception:
        pass

    # 2) Now AutoTune (sees KPI and produces accurate telemetry)
    if getattr(CONFIG, "autotune_enabled", False):
        try:
            summary = autotune_config(
                CONFIG,
                api_key=api_key,
                api_secret=api_secret,
                portfolio_id=portfolio_id,
                preview_only=getattr(CONFIG, "autotune_preview_only", True),
            )
            log.info(
                "AUTOTUNE: mode=%s | regime=%s | winner=%s | share=%.2f | alpha=%.2f",
                summary.get("mode"),
                summary.get("portfolio_regime"),
                summary.get("winner"),
                float(summary.get("share") or 0.0),
                float(summary.get("alpha") or 0.0),
            )
            log.info("AUTOTUNE votes: %s", summary.get("portfolio_vote"))
            log.info("AUTOTUNE knob changes: %s", summary.get("global_changes"))

            # Offsets: guard + show only active coins
            active = set(getattr(CONFIG, "coin_ids", []))
            offsets_all = summary.get("offsets_changed") or summary.get("offsets") or {}
            if isinstance(offsets_all, dict):
                offsets_active = {k: v for k, v in offsets_all.items() if k in active}
                if offsets_active:
                    log.info("AUTOTUNE offsets (active only): %s", offsets_active)
                else:
                    log.info("AUTOTUNE offsets: (none for active set)")
            else:
                log.info("AUTOTUNE offsets: (not provided)")

            # Optional advisory disables
            cands = (summary.get("disabled_coins") or []) if isinstance(summary, dict) else []
            details = (summary.get("disabled_details") or {}) if isinstance(summary, dict) else {}
            if cands:
                pretty = ", ".join(f"{p}({details.get(p,'')})" if p in details else p for p in cands)
                log.info("AUTOTUNE (advisory only) would disable: %s", pretty)
        except Exception as e:
            log.warning("Autotune failed (continuing with current config): %s", e)

    # 3) Open websocket + subscribe (prints “Subscribed … / WS ready”)
    try:
        bot.open()
    except Exception as e:
        log.exception("Failed to open websocket: %s", e)
        _request_shutdown(bot, 1)
        
    # Optional: one-shot elapsed AutoTune refresh
    if AUTOTUNE_ELAPSED_REFRESH_ENABLED and getattr(CONFIG, "autotune_enabled", False):
        t = threading.Thread(
            target=_elapsed_autotune_once_with_bot,
            args=(bot, api_key, api_secret, portfolio_id),
            name="autotune-elapsed",
            daemon=True,
        )
        t.start()

    # --- Mid-session reconcile (restores v1.0.4 behavior) ---
    if getattr(CONFIG, "mid_reconcile_enabled", True):

        def _periodic_reconcile():
            interval_min = int(getattr(CONFIG, "mid_reconcile_interval_minutes", 90))
            interval_s = max(60, 60 * interval_min)
            step = 5  # small sleep steps so Ctrl+C is responsive

            while not _shutdown_once.is_set():
                slept = 0
                while slept < interval_s and not _shutdown_once.is_set():
                    time.sleep(step)
                    slept += step
                if _shutdown_once.is_set():
                    break

                lookback_inner = 2  # Only lookback 2 hours every hour for mid-session reconcile

                def _threaded_reconcile():
                    try:
                        log.info("Mid-session reconcile sweep...")
                        if hasattr(bot, "reconcile_now"):
                            bot.reconcile_now(hours=lookback_inner)
                        else:
                            bot.reconcile_recent_fills(lookback_inner)
                    except Exception as e:
                        logging.getLogger("main").warning("Mid-session reconcile failed: %s", e)

                threading.Thread(
                    target=_threaded_reconcile,
                    name=f"reconcile-{int(time.time())}",
                    daemon=True
                ).start()

        threading.Thread(target=_periodic_reconcile, daemon=True, name="mid_reconcile").start()

    # Blocking WS loop — Windows-friendly Ctrl+C
    try:
        bot.run_ws_forever()
        # If the loop ever returns normally, exit cleanly.
        _request_shutdown(bot, 0)
    except KeyboardInterrupt:
        logging.getLogger("tradebot").info("Ctrl+C received; waiting for websocket loop to exit...")
        _request_shutdown(bot, 0)
    except Exception as e:
        logging.getLogger("tradebot").exception("Fatal error in run loop: %s", e)
        _request_shutdown(bot, 1)

if __name__ == "__main__":
    main()
