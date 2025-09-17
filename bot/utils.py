# bot/utils.py
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

# ----------------------------------
# Display precision for realized P&L
# ----------------------------------
PNL_DECIMALS = 8  # show realized P&L to 8 decimal places

# ----------------------------------
# State files
# ----------------------------------
STATE_DIR = Path(".state")
STATE_DIR.mkdir(exist_ok=True)

DAILY_FILE = STATE_DIR / "daily_spend.json"
LASTTRADE_FILE = STATE_DIR / "last_trades.json"
TRADE_LOG_FILE = STATE_DIR / "trade_log.txt"
PORTFOLIO_FILE = STATE_DIR / "portfolio.json"              # positions, cost basis, *realized* P&L (live)
PROCESSED_FILLS_FILE = STATE_DIR / "processed_fills.json"  # dedupe processed fills


# -----------------------
# JSON helpers
# -----------------------
def load_json(path: Path, default: Any):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def save_json(path: Path, data: Any):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


# -----------------------
# Trade log helper
# -----------------------
def log_trade(product_id: str, side: str, usd_amount: float, price: float, quantity: float, dry_run: bool):
    """
    Append a single-line trade entry to TRADE_LOG_FILE.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    entry = (
        f"{ts} | {side:<4} {product_id:<10} "
        f"USD ${usd_amount:.2f} @ ${price:.6f} "
        f"Qty {quantity:.8f} "
        f"{'(DRY RUN)' if dry_run else ''}\n"
    )
    with open(TRADE_LOG_FILE, "a") as f:
        f.write(entry)


# -----------------------
# Daily spend tracker
# -----------------------
class SpendTracker:
    """
    Tracks daily spend across runs. Uses UTC date.
    """
    def __init__(self):
        self.data: Dict[str, float] = load_json(DAILY_FILE, {})

    def _day_key(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def add(self, usd: float):
        k = self._day_key()
        self.data.setdefault(k, 0.0)
        self.data[k] += float(usd)
        save_json(DAILY_FILE, self.data)

    def today_total(self) -> float:
        return float(self.data.get(self._day_key(), 0.0))


# -----------------------
# Per-product cooldown tracker
# -----------------------
class LastTradeTracker:
    """
    Stores the last trade timestamp per product to enforce cooldowns.
    """
    def __init__(self):
        self.data: Dict[str, float] = load_json(LASTTRADE_FILE, {})

    def ok(self, product_id: str, cooldown_sec: int) -> bool:
        t = self.data.get(product_id)
        if not t:
            return True
        return (time.time() - float(t)) >= cooldown_sec

    def stamp(self, product_id: str):
        self.data[product_id] = time.time()
        save_json(LASTTRADE_FILE, self.data)
