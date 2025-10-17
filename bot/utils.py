# bot/utils.py
# Compatibility layer that re-exports constants/persistence helpers
# …and provides REST retry + pacing utilities (TokenBucket, RestProxy)

from __future__ import annotations
import logging
import random
import threading
import time
from typing import Any, Iterable

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

__all__ = [
    # re-exports
    "PNL_DECIMALS",
    "DAILY_FILE",
    "LASTTRADE_FILE",
    "TRADE_LOG_FILE",
    "PORTFOLIO_FILE",
    "PROCESSED_FILLS_FILE",
    "TRADES_CSV_FILE",
    "load_json",
    "save_json",
    "log_trade",
    "SpendTracker",
    "LastTradeTracker",
    # new
    "TokenBucket",
    "RestProxy",
]

# -----------------------------
# REST pacing & retry helpers
# -----------------------------

class TokenBucket:
    """
    Simple token bucket for soft RPS limits.
    capacity: max tokens
    refill_rate: tokens per second
    """
    def __init__(self, capacity: float, refill_rate: float):
        self.capacity = float(capacity)
        self.tokens = float(capacity)
        self.refill_rate = float(refill_rate)
        self._lock = threading.Lock()
        self._last = time.time()

    def take(self, cost: float = 1.0) -> float:
        """
        Consume 'cost' tokens. Returns 0 if enough tokens were available,
        otherwise returns the number of seconds the caller should sleep
        before trying again.
        """
        with self._lock:
            now = time.time()
            dt = max(0.0, now - self._last)
            self._last = now
            # refill
            self.tokens = min(self.capacity, self.tokens + dt * self.refill_rate)
            if self.tokens >= cost:
                self.tokens -= cost
                return 0.0
            # not enough: compute wait time
            shortfall = cost - self.tokens
            wait_s = shortfall / max(1e-9, self.refill_rate)
            self.tokens = 0.0
            return wait_s


def _jitter_ms(lo_ms: int, hi_ms: int) -> float:
    return random.uniform(lo_ms, hi_ms) / 1000.0


class RestProxy:
    """
    Wraps a Coinbase REST client with:
      - token-bucket pacing (soft RPS limit),
      - bounded retries with jittered backoff on network/5xx/429 errors.

    Usage:
        rest = RESTClient(...)              # your original client
        safe_rest = RestProxy(rest, attempts=3, rps_soft_limit=8.0)
        safe_rest.get_fills(...)           # transparently wrapped

    Only callables are wrapped; other attributes pass through.
    """
    def __init__(
        self,
        rest: Any,
        *,
        attempts: int = 3,
        backoff_min_ms: int = 200,
        backoff_max_ms: int = 600,
        retry_statuses: Iterable[int] = (429, 500, 502, 503, 504),
        rps_soft_limit: float = 8.0,
    ):
        self._rest = rest
        self._attempts = int(max(1, attempts))
        self._bo_min = int(backoff_min_ms)
        self._bo_max = int(backoff_max_ms)
        self._retry_statuses = set(int(s) for s in retry_statuses or ())
        # capacity=limit, refill_rate=limit tokens/sec → ~limit RPS
        self._bucket = TokenBucket(capacity=rps_soft_limit, refill_rate=rps_soft_limit)

    def __getattr__(self, name: str):
        attr = getattr(self._rest, name)
        if not callable(attr):
            return attr

        def _wrapped(*args, **kwargs):
            # Soft pacing (sleep if bucket is empty)
            wait_s = self._bucket.take(1.0)
            if wait_s > 0:
                logging.debug("REST pacing: sleeping %.3fs to respect soft RPS.", wait_s)
                time.sleep(wait_s)

            attempt = 0
            while True:
                attempt += 1
                try:
                    return attr(*args, **kwargs)
                except Exception as e:
                    # Coinbase SDK exceptions often carry .status or .response.status
                    status = getattr(e, "status", None)
                    if status is None:
                        # try response-like objects
                        resp = getattr(e, "response", None)
                        status = getattr(resp, "status", None) or getattr(resp, "status_code", None)

                    should_retry = (
                        attempt < self._attempts and
                        (status is None or int(status) in self._retry_statuses)
                    )

                    if not should_retry:
                        raise

                    backoff = _jitter_ms(self._bo_min, self._bo_max)
                    logging.warning(
                        "REST call %s failed (status=%s, attempt=%d/%d). Backing off %.3fs…",
                        name, status, attempt, self._attempts, backoff
                    )
                    time.sleep(backoff)

        return _wrapped