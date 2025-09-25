# bot/utils.py
# Thin compatibility layer that re-exports constants and persistence helpers

from .constants import (
    PNL_DECIMALS,
    DAILY_FILE,
    LASTTRADE_FILE,
    TRADE_LOG_FILE,
    PORTFOLIO_FILE,
    PROCESSED_FILLS_FILE,
    TRADES_CSV_FILE,
)

from .persistence import (
    load_json,
    save_json,
    log_trade_line as log_trade,
    SpendTracker,
    LastTradeTracker,
)
