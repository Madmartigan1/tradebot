# bot/constants.py
import os
from pathlib import Path

PNL_DECIMALS = 8

# Default to <repo>/.state; allow override via BOT_STATE_DIR
_default_state = (Path(__file__).resolve().parent / ".." / ".state").resolve()
STATE_DIR = Path(os.getenv("BOT_STATE_DIR", str(_default_state)))

try:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

DAILY_FILE = STATE_DIR / "daily_spend.json"
LASTTRADE_FILE = STATE_DIR / "last_trades.json"
TRADE_LOG_FILE = STATE_DIR / "trade_log.txt"
PORTFOLIO_FILE = STATE_DIR / "portfolio.json"
PROCESSED_FILLS_FILE = STATE_DIR / "processed_fills.json"
TRADES_CSV_FILE = STATE_DIR / "trades.csv"
