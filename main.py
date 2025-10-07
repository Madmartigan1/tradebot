# main.py (v1.0.7 — APIkeys.env like v1.0.4; hybrid AutoTune; Windows-friendly Ctrl+C; telemetry with detail added)
import os
import sys
import time
import threading
import logging
import signal
from dotenv import load_dotenv

from bot.config import CONFIG
from bot.autotune import autotune_config
from bot.tradebot import TradeBot

# Optional elapsed-time AutoTune refresh (one-shot after N hours)
AUTOTUNE_ELAPSED_REFRESH_ENABLED = True
AUTOTUNE_ELAPSED_REFRESH_HOURS = 4

_shutdown_once = threading.Event()
_run_start_monotonic = time.monotonic()


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
        sys.exit(1)

    return api_key, api_secret, portfolio_id


def _elapsed_autotune_once():
    logger = logging.getLogger("tradebot.autotune-elapsed")
    try:
        from bot.autotune import autotune_config as _ac
    except Exception:
        _ac = None
    if _ac is None or not getattr(CONFIG, "autotune_enabled", False):
        return

    target_sec = int(AUTOTUNE_ELAPSED_REFRESH_HOURS * 3600)
    step = 5
    while not _shutdown_once.is_set():
        if (time.monotonic() - _run_start_monotonic) >= target_sec:
            break
        time.sleep(step)

    try:
        # --- begin temporary 6h lookback override for elapsed re-tune ---
        _orig_lb = getattr(CONFIG, "autotune_lookback_hours", 18)
        _elapsed_lb = getattr(CONFIG, "elapsed_autotune_lookback_hours", _orig_lb)
        setattr(CONFIG, "autotune_lookback_hours", _elapsed_lb)

        summary = _ac(
            CONFIG,
            api_key=api_key,
            api_secret=api_secret,
            portfolio_id=portfolio_id,
            preview_only=getattr(CONFIG, "autotune_preview_only", True),
        )
    finally:
        # always restore the original startup lookback
        setattr(CONFIG, "autotune_lookback_hours", _orig_lb)

    logger.info(
        "AUTOTUNE(elapsed %dh using %dh lookback): mode=%s | regime=%s | winner=%s | share=%.2f | alpha=%.2f",
        AUTOTUNE_ELAPSED_REFRESH_HOURS,
        _elapsed_lb,
        summary.get("mode"),
        summary.get("portfolio_regime"),
        summary.get("winner"),
        float(summary.get("share") or 0.0),
        float(summary.get("alpha") or 0.0),
    )
    logger.info("AUTOTUNE(elapsed) votes: %s", summary.get("portfolio_vote"))
    logger.info("AUTOTUNE(elapsed) knob changes: %s", summary.get("global_changes"))
    logger.info("AUTOTUNE(elapsed) offsets (post 3d KPI nudges): %s", summary.get("offsets_changed"))
    if summary.get("disabled_products"):
        logger.info("AUTOTUNE(elapsed, advisory only) would disable: %s", summary.get("disabled_products"))



def main():
    log = _setup_logging()

    # POSIX signals only on non-Windows; on Windows rely on KeyboardInterrupt below
    if os.name != "nt":
        def _sigterm(_signo, _frame):
            _shutdown_once.set()
        signal.signal(signal.SIGINT, _sigterm)
        signal.signal(signal.SIGTERM, _sigterm)

    # --- v1.0.4 key loading (from APIkeys.env) ---
    api_key, api_secret, portfolio_id = _load_keys_from_envfile()

    # Construct the bot (TradeBot builds REST+WS client using api_key/api_secret)
    bot = TradeBot(CONFIG, api_key=api_key, api_secret=api_secret, portfolio_id=portfolio_id)

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
            log.info("AUTOTUNE offsets (post 3d KPI nudges): %s", summary.get("offsets_changed"))
            if summary.get("disabled_products"):
                cands = summary.get("disabled_products") or []
                details = summary.get("disabled_details") or {}
                if cands:
                    pretty = ", ".join(f"{p}({details.get(p,'')})" if p in details else p for p in cands)
                    log.info("AUTOTUNE (advisory only) would disable: %s", pretty)
        except Exception as e:
            log.warning("Autotune failed (continuing with current config): %s", e)

    # 3) Open websocket + subscribe (prints “Subscribed … / WS ready”)
    bot.open()

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

                try:
                    lookback_inner = int(getattr(CONFIG, "lookback_hours", 48))
                    log.info("Mid-session reconcile sweep...")
                    if hasattr(bot, "reconcile_now"):
                        bot.reconcile_now(hours=lookback_inner)
                    else:
                        bot.reconcile_recent_fills(lookback_inner)
                except Exception as e:
                    logging.getLogger("main").warning("Mid-session reconcile failed: %s", e)

        threading.Thread(target=_periodic_reconcile, daemon=True, name="mid_reconcile").start()

    # 4) Optional elapsed-time re-tune
    if AUTOTUNE_ELAPSED_REFRESH_ENABLED and getattr(CONFIG, "autotune_enabled", False):
        threading.Thread(target=_elapsed_autotune_once, daemon=True, name="elapsed_autotune").start()

    # 5) Blocking WS loop — Windows-friendly Ctrl+C
    try:
        bot.run_ws_forever()
    except KeyboardInterrupt:
        logging.getLogger("tradebot").info("Ctrl+C pressed; shutting down...")
        try:
            if hasattr(bot, "close"):
                bot.close()
        finally:
            sys.exit(0)
    finally:
        _finalize_and_exit()


if __name__ == "__main__":
    main()
