# main.py
import os
import sys
import asyncio
import logging
from dotenv import load_dotenv

from bot.config import CONFIG
from bot.tradebot import TradeBot

async def main():
    # Load API keys
    load_dotenv(dotenv_path="APIkeys.env")
    api_key = os.getenv("COINBASE_API_KEY")
    api_secret = os.getenv("COINBASE_API_SECRET")
    portfolio_id = os.getenv("COINBASE_PORTFOLIO_ID") or None

    if not api_key or not api_secret:
        print("ERROR: Missing COINBASE_API_KEY or COINBASE_API_SECRET in environment.")
        sys.exit(2)

    # Logging setup
    logging.basicConfig(
        level=CONFIG.log_level,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    bot = TradeBot(CONFIG, api_key, api_secret, portfolio_id=portfolio_id)

    # Reconcile past fills before starting
    bot.reconcile_recent_fills(lookback_hours=CONFIG.lookback_hours)
    bot.set_run_baseline()

    try:
        bot.open()
        while not bot.stop_requested:
            await asyncio.sleep(1)
        logging.info("Shutting down...")
    except KeyboardInterrupt:
        logging.info("Shutting down...")
    finally:
        bot.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())
