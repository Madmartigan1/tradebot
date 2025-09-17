from pathlib import Path

# Display precision for realized P&L
PNL_DECIMALS = 8

# State files
STATE_DIR = Path(".state")
STATE_DIR.mkdir(exist_ok=True)

DAILY_FILE = STATE_DIR / "daily_spend.json"
LASTTRADE_FILE = STATE_DIR / "last_trades.json"
TRADE_LOG_FILE = STATE_DIR / "trade_log.txt"
PORTFOLIO_FILE = STATE_DIR / "portfolio.json"
PROCESSED_FILLS_FILE = STATE_DIR / "processed_fills.json"
