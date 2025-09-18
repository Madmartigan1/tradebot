# main.py
import os
import sys
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

def main():
    setup_logging()
    log = logging.getLogger("main")
    load_dotenv(dotenv_path=os.getenv("ENV_PATH", "APIkeys.env"))

    api_key = os.getenv("COINBASE_API_KEY")
    api_secret = os.getenv("COINBASE_API_SECRET")
    portfolio_id = os.getenv("PORTFOLIO_ID")

    if not api_key or not api_secret:
        log.error("Missing COINBASE_API_KEY / COINBASE_API_SECRET in environment.")
        sys.exit(1)

    bot = TradeBot(CONFIG, api_key=api_key, api_secret=api_secret, portfolio_id=portfolio_id)
    bot.set_run_baseline()
    
    def handle_signal(sig, frame):
        log.info("Signal %s received. Closing bot...", sig)
        bot.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Open and subscribe, then hand control to the SDK's run loop.
    bot.open()    
    # --- OPTIONAL backfill on STARTUP (useful if you run the bot 2–3 short sessions/day and open orders remain to be filled.) ---
    # --- If there are no pending open orders for a next run, leave it commented out.
    try:
       log.info("Gathering trade data from past %s hours...", CONFIG.lookback_hours)
       bot.reconcile_recent_fills(CONFIG.lookback_hours)
    except Exception as e:
       log.warning("Reconcile on startup failed: %s", e)
    # ----------------------------------------------------------------------------------------
    try:
        bot.run_ws_forever()
    finally:
        try:
            bot.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
