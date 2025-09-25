# main.py
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

# === Optional: elapsed-time AutoTune refresh (one-time) =================
AUTOTUNE_ELAPSED_REFRESH_ENABLED = True
AUTOTUNE_ELAPSED_REFRESH_HOURS = 4      # re-tune after 4 hours of runtime
# ========================================================================

def setup_logging():
    logging.basicConfig(
        level=CONFIG.log_level,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def main():
    setup_logging()
    log = logging.getLogger("main")

    # Same env behavior as your original: ENV_PATH override or APIkeys.env
    load_dotenv(dotenv_path=os.getenv("ENV_PATH", "APIkeys.env"))

    api_key = os.getenv("COINBASE_API_KEY")
    api_secret = os.getenv("COINBASE_API_SECRET")
    portfolio_id = os.getenv("PORTFOLIO_ID")

    if not api_key or not api_secret:
        log.error("Missing COINBASE_API_KEY / COINBASE_API_SECRET in environment.")
        sys.exit(1)

    # --- AUTOTUNE: run BEFORE constructing TradeBot (same as your original) ---
    if getattr(CONFIG, "autotune_enabled", False):
        try:
            summary = autotune_config(
                CONFIG,
                api_key=api_key,
                api_secret=api_secret,
                portfolio_id=getattr(CONFIG, "portfolio_id", None),
                preview_only=getattr(CONFIG, "autotune_preview_only", True),
            )
            log.info(
                "AUTOTUNE: regime=%s vote=%s changes=%s disabled=%s",
                summary.get("portfolio_regime"),
                summary.get("portfolio_vote"),
                summary.get("global_changes"),
                summary.get("disabled_products"),
            )
            log.info("AUTOTUNE (advisory only) would disable: %s", summary.get("disabled_products"))
        except Exception as e:
            log.warning("Autotune failed (continuing with current config): %s", e)

    # Construct the bot AFTER autotune has (optionally) adjusted CONFIG
    bot = TradeBot(CONFIG, api_key=api_key, api_secret=api_secret, portfolio_id=portfolio_id)
    bot.set_run_baseline()

    # ---- Idempotent shutdown guard so finalize runs exactly once ----
    _shutdown_once = threading.Event()

    # Final reconcile lookback (hours)
    FINAL_RECONCILE_LOOKBACK_HOURS = 2  # small/cheap tail sweep

    def _finalize_and_exit(sig=None):
        if _shutdown_once.is_set():
            return
        _shutdown_once.set()
        # 1) Final reconcile (short tail) -> keep output tidy by muting noisy INFO inside
        try:
            log.info("Final reconcile...")
            root_logger = logging.getLogger()
            prev_level = root_logger.level
            try:
                if prev_level < logging.WARNING:
                    root_logger.setLevel(logging.WARNING)
                bot.reconcile_now(hours=FINAL_RECONCILE_LOOKBACK_HOURS)
            finally:
                root_logger.setLevel(prev_level)
        except Exception as e:
            log.warning("Final reconcile failed: %s", e)
        # 2) Close (lets TradeBot.close() print the P&L as in your original)
        try:
            bot.close()
        finally:
            # 3) Exit message
            log.info("Exiting bot...")
            sys.exit(0)

    # Keep signal handler quiet; just run the finalizer
    def handle_signal(sig, frame):
        _finalize_and_exit(sig)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Open WS, subscribe
    bot.open()

    # Startup reconcile (sweep fills from offline period) — same as before
    try:
        log.info("Gathering trade data from past %s hours...", CONFIG.lookback_hours)
        bot.reconcile_recent_fills(CONFIG.lookback_hours)
    except Exception as e:
        log.warning("Reconcile on startup failed: %s", e)

    # --- Mid-session periodic reconcile (default 90 minutes) ---
    def _periodic_reconcile():
        # use CONFIG.mid_reconcile_interval_minutes if set; else default 90
        interval_min = int(getattr(CONFIG, "mid_reconcile_interval_minutes", 90))
        interval_s = max(60, 60 * interval_min)
        while not _shutdown_once.is_set():
            # sleep in small steps so Ctrl+C is responsive
            slept = 0
            step = 5
            while slept < interval_s and not _shutdown_once.is_set():
                time.sleep(step)
                slept += step
            if _shutdown_once.is_set() or not getattr(CONFIG, "mid_reconcile_enabled", True):
                break
            try:
                log.info("Mid-session reconcile sweep...")
                bot.reconcile_now(hours=getattr(CONFIG, "lookback_hours", 48))
            except Exception as e:
                logging.getLogger("main").warning("Mid-session reconcile failed: %s", e)

    if getattr(CONFIG, "mid_reconcile_enabled", True):
        t = threading.Thread(target=_periodic_reconcile, daemon=True, name="mid_reconcile")
        t.start()
        
    # Record start time for elapsed refresh
    run_start_monotonic = time.monotonic()

    # ---- One-time elapsed AutoTune refresh (after N hours of runtime) ----
    def _elapsed_autotune_once():
        if not AUTOTUNE_ELAPSED_REFRESH_ENABLED:
            return
        # must have autotune available and enabled
        try:
            from bot.autotune import autotune_config as _ac
        except Exception:
            _ac = None
        if _ac is None or not getattr(CONFIG, "autotune_enabled", False):
            return

        # Wait until elapsed >= target, sleeping in small steps so Ctrl+C is snappy
        target_sec = int(AUTOTUNE_ELAPSED_REFRESH_HOURS * 3600)
        step = 5
        while not _shutdown_once.is_set():
            elapsed = time.monotonic() - run_start_monotonic
            if elapsed >= target_sec:
                break
            time.sleep(step)

        if _shutdown_once.is_set():
            return

        logger = logging.getLogger("main")
        try:
            summary = _ac(
                CONFIG,
                api_key=api_key,
                api_secret=api_secret,
                portfolio_id=getattr(CONFIG, "portfolio_id", None),
                preview_only=getattr(CONFIG, "autotune_preview_only", True),
            )
            logger.info(
                "AUTOTUNE(elapsed %dh): regime=%s vote=%s changes=%s disabled=%s",
                AUTOTUNE_ELAPSED_REFRESH_HOURS,
                summary.get("portfolio_regime"),
                summary.get("portfolio_vote"),
                summary.get("global_changes"),
                summary.get("disabled_products"),
            )
            if getattr(CONFIG, "autotune_preview_only", True):
                logger.info(
                    "AUTOTUNE(elapsed, advisory only) would disable: %s",
                    summary.get("disabled_products"),
                )
        except Exception as e:
            logger.warning("Elapsed autotune failed: %s", e)

    # Kick off the elapsed-time autotune thread
    if AUTOTUNE_ELAPSED_REFRESH_ENABLED:
        threading.Thread(
            target=_elapsed_autotune_once, daemon=True, name="elapsed_autotune"
        ).start()


    # Hand control to SDK run loop
    try:
        bot.run_ws_forever()
    finally:
        _finalize_and_exit()


if __name__ == "__main__":
    main()
