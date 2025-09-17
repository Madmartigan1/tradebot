# main.py
import os
import asyncio
import logging
from dotenv import load_dotenv

from bot.tradebot import TradeBot
from bot.config import CONFIG

load_dotenv("APIkeys.env")
API_KEY = os.getenv("COINBASE_API_KEY")
API_SECRET = os.getenv("COINBASE_API_SECRET")

async def main():
    logging.basicConfig(
        level=CONFIG.log_level,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    bot = TradeBot(CONFIG, api_key=API_KEY, api_secret=API_SECRET)

    bot.reconcile_recent_fills(lookback_hours=48)
    bot.set_run_baseline()

    try:
        bot.open()
        while not bot.stop_requested:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logging.info("Shutting down...")
    finally:
        bot.close()

if __name__ == "__main__":
    asyncio.run(main())
