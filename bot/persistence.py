import json, time
from collections import defaultdict
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Dict, Any

from .constants import (
    DAILY_FILE, LASTTRADE_FILE, TRADE_LOG_FILE, PORTFOLIO_FILE, PROCESSED_FILLS_FILE, PNL_DECIMALS
)

def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default

def save_json(path, data: Any):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)

def log_trade_line(product_id: str, side: str, usd_amount: float, price: float, quantity: float, dry_run: bool):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    entry = (
        f"{ts} | {side:<4} {product_id:<10} "
        f"USD ${usd_amount:.2f} @ ${price:.6f} "
        f"Qty {quantity:.8f} "
        f"{'(DRY RUN)' if dry_run else ''}\n"
    )
    with open(TRADE_LOG_FILE, "a") as f:
        f.write(entry)

class SpendTracker:
    def __init__(self):
        self.data = load_json(DAILY_FILE, {})

    def _day_key(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def add(self, usd: float):
        k = self._day_key()
        self.data.setdefault(k, 0.0)
        self.data[k] += float(usd)
        save_json(DAILY_FILE, self.data)

    def today_total(self) -> float:
        return float(self.data.get(self._day_key(), 0.0))

class LastTradeTracker:
    def __init__(self):
        self.data = load_json(LASTTRADE_FILE, {})

    def ok(self, product_id: str, cooldown_sec: int) -> bool:
        t = self.data.get(product_id)
        if not t:
            return True
        return (time.time() - float(t)) >= cooldown_sec

    def stamp(self, product_id: str):
        self.data[product_id] = time.time()
        save_json(LASTTRADE_FILE, self.data)

@dataclass
class PortfolioStore:
    positions: Dict[str, float]
    cost_basis: Dict[str, float]
    realized_pnl: float

    @classmethod
    def load(cls):
        data = load_json(PORTFOLIO_FILE, {"positions": {}, "cost_basis": {}, "realized_pnl": 0.0})
        pos = {k: float(v) for k, v in data.get("positions", {}).items()}
        cb  = {k: float(v) for k, v in data.get("cost_basis", {}).items()}
        rpnl = float(data.get("realized_pnl", 0.0))
        return cls(pos, cb, rpnl)

    def save(self):
        save_json(PORTFOLIO_FILE, {
            "positions": self.positions,
            "cost_basis": self.cost_basis,
            "realized_pnl": float(self.realized_pnl),
        })

class ProcessedFills:
    def __init__(self):
        self.idx = load_json(PROCESSED_FILLS_FILE, {})

    def has(self, fp: str) -> bool:
        return fp in self.idx

    def add(self, fp: str, meta: Dict):
        self.idx[fp] = meta
        save_json(PROCESSED_FILLS_FILE, self.idx)
