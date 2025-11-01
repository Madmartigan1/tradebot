# bot/persistence.py
import json, os, io, time
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Dict, Any, Iterable, Tuple
from pathlib import Path
from collections import OrderedDict

from .constants import (
    DAILY_FILE, LASTTRADE_FILE, TRADE_LOG_FILE, PORTFOLIO_FILE, PROCESSED_FILLS_FILE, PNL_DECIMALS
)

def _ensure_parent(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)

def load_json(path: Path, default: Any):
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text)
    except (FileNotFoundError, json.JSONDecodeError):
        return default
    except Exception:
        return default

def save_json(path: Path, data: Any):
    _ensure_parent(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    b = json.dumps(data, indent=2).encode("utf-8")
    with open(tmp, "wb") as f:
        f.write(b); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)

def _rotate_if_big(log_path: Path, max_mb: int = 10, backups: int = 3):
    try:
        if not log_path.exists() or log_path.stat().st_size < max_mb * 1024 * 1024:
            return
        for i in range(backups, 0, -1):
            src = log_path.with_suffix(log_path.suffix + f".{i}")
            dst = log_path.with_suffix(log_path.suffix + f".{i+1}")
            if src.exists():
                if i == backups:
                    src.unlink(missing_ok=True)
                else:
                    os.replace(src, dst)
        os.replace(log_path, log_path.with_suffix(log_path.suffix + ".1"))
    except Exception:
        pass

def log_trade_line(coin_id: str, side: str, usd_amount: float, price: float, quantity: float, dry_run: bool):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    entry = (
        f"{ts} | {side:<4} {coin_id:<10} "
        f"USD ${usd_amount:.2f} @ ${price:.6f} "
        f"Qty {quantity:.8f} "
        f"{'(DRY RUN)' if dry_run else ''}\n"
    )
    _ensure_parent(TRADE_LOG_FILE)
    _rotate_if_big(TRADE_LOG_FILE, max_mb=10)
    with open(TRADE_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(entry)

class SpendTracker:
    def __init__(self, retention_days: int = 14):
        self.retention_days = retention_days
        self.data = load_json(DAILY_FILE, {})

    def _day_key(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def add(self, usd: float):
        k = self._day_key()
        self.data.setdefault(k, 0.0)
        self.data[k] += float(usd)
        self._prune_old()
        save_json(DAILY_FILE, self.data)

    def today_total(self) -> float:
        return float(self.data.get(self._day_key(), 0.0))

    def _prune_old(self):
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
            keep = {}
            for k, v in self.data.items():
                try:
                    dt = datetime.strptime(k, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    if dt >= cutoff:
                        keep[k] = v
                except Exception:
                    pass
            self.data = keep
        except Exception:
            pass

class LastTradeTracker:
    def __init__(self):
        self.data = load_json(LASTTRADE_FILE, {})

    def ok(self, coin_id: str, cooldown_sec: int) -> bool:
        t = self.data.get(coin_id)
        if not t:
            return True
        try:
            return (time.time() - float(t)) >= cooldown_sec
        except Exception:
            return True

    def stamp(self, coin_id: str):
        self.data[coin_id] = time.time()
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
    """
    Lightweight index of processed fill fingerprints -> metadata.
    Designed for append-heavy writes with occasional pruning of oldest items.
    """
    def __init__(self, initial: Dict[str, Dict[str, Any]] | None = None):
        # Keep oldest->newest order so prune() removes the oldest first
        self.idx: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        if isinstance(initial, dict):
            def _epoch(meta: Dict[str, Any]) -> float:
                t = meta.get("t")
                try:
                    if isinstance(t, (int, float)):
                        return float(t)
                    if isinstance(t, str):
                        dt = datetime.fromisoformat(t)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        return dt.timestamp()
                except Exception:
                    pass
                return 0.0
            # Load oldest-first for deterministic pruning
            for k, v in sorted(initial.items(), key=lambda kv: _epoch(kv[1])):
                self.idx[k] = v

    # --- core API expected by tradebot.py ---
    def has(self, key: str) -> bool:
        """Return True if key already recorded."""
        return key in self.idx

    def add(self, key: str, meta: Dict[str, Any]) -> None:
        """
        Record a processed fill fingerprint with metadata.
        If key exists, update metadata and move to the end (newest).
        """
        if key in self.idx:
            # update in place, then move to end to reflect "latest seen"
            self.idx[key].update(meta or {})
            self.idx.move_to_end(key, last=True)
        else:
            self.idx[key] = dict(meta or {})

    def prune(self, max_keys: int = 10_000) -> None:
        """Drop oldest entries until size <= max_keys."""
        n = len(self.idx)
        if n <= max_keys:
            return
        drop = n - max_keys
        for _ in range(drop):
            # pop oldest (leftmost)
            self.idx.popitem(last=False)

    def to_dict(self) -> Dict[str, Dict[str, Any]]:
        """Return a plain dict for JSON persistence."""
        return dict(self.idx)

    # --- convenience methods (optional but handy) ---
    def __len__(self) -> int:
        return len(self.idx)

    def __contains__(self, key: str) -> bool:
        return key in self.idx

    def items(self) -> Iterable[Tuple[str, Dict[str, Any]]]:
        return self.idx.items()
        


