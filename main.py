# main.py
import os
import sys
import asyncio
import logging
import signal
from dotenv import load_dotenv

from bot.config import CONFIG
from bot.tradebot import TradeBot

def setup_logging():
    logging.basicConfig(
        level=CONFIG.log_level,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

def load_secrets():
    # Allow an override; fall back to environment-only if file missing.
    env_path = os.getenv("ENV_FILE", "APIkeys.env")
    try:
        load_dotenv(dotenv_path=env_path)
    except Exception:
        # Not fatal—env vars might already be set.
        pass

    api_key = os.getenv("COINBASE_API_KEY")
    api_secret = os.getenv("COINBASE_API_SECRET")
    portfolio_id = os.getenv("COINBASE_PORTFOLIO_ID") or None

    if not api_key or not api_secret:
        print("ERROR: Missing COINBASE_API_KEY or COINBASE_API_SECRET in environment.")
        sys.exit(2)

    return api_key, api_secret, portfolio_id

def install_signal_handlers(bot: TradeBot, loop: asyncio.AbstractEventLoop):
    # Flip bot.stop_requested on SIGINT/SIGTERM when supported.
    def _request_stop():
        try:
            bot.stop_requested = True
        except Exception:
            # If your TradeBot exposes a method instead, swap this call:
            # bot.request_stop()
            pass

    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            # Windows / certain environments don’t support it; rely on KeyboardInterrupt.
            pass

async def run_bot():
    setup_logging()
    log = logging.getLogger("main")

    api_key, api_secret, portfolio_id = load_secrets()

    # Single instantiation (removed duplicate)
    bot = TradeBot(CONFIG, api_key, api_secret, portfolio_id=portfolio_id)
    loop = asyncio.get_running_loop()
    install_signal_handlers(bot, loop)

    # Reconcile/initialize
    bot.reconcile_recent_fills(lookback_hours=CONFIG.lookback_hours)
    bot.set_run_baseline()

    try:
        log.info("Opening bot…")
        bot.open()  # should return quickly (start threads/WS and return)
        while not bot.stop_requested:
            await asyncio.sleep(1)
        log.info("Stop requested; shutting down…")
    except KeyboardInterrupt:
        log.info("Keyboard interrupt; shutting down…")
    finally:
        try:
            bot.close()
        except Exception as e:
            log.exception("Error during bot.close(): %s", e)

def _fallback_run(coro):
    # Fallback for environments where asyncio.run() isn’t allowed.
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(coro)

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except RuntimeError:
        _fallback_run(run_bot())
