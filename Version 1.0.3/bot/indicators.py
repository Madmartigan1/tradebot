# bot/indicators.py
from __future__ import annotations
from typing import Optional, Tuple

class RSI:
    def __init__(self, period: int = 14):
        if period <= 0:
            raise ValueError("RSI period must be positive")
        self.period = int(period)
        self._prev_price: Optional[float] = None
        self._avg_gain: Optional[float] = None
        self._avg_loss: Optional[float] = None
        self._deltas_seen = 0
        self.value: Optional[float] = None

    @property
    def ready(self) -> bool:
        return self.value is not None

    @property
    def warmup_left(self) -> int:
        return max(0, self.period - self._deltas_seen)

    def reset(self):
        self._prev_price = None
        self._avg_gain = None
        self._avg_loss = None
        self._deltas_seen = 0
        self.value = None

    def seed(self, price: float):
        self._prev_price = float(price)
        self.value = None

    def update(self, price: float) -> Optional[float]:
        p = float(price)
        if self._prev_price is None:
            self._prev_price = p
            self._deltas_seen = 0
            self.value = None
            return None

        change = p - self._prev_price
        gain = change if change > 0.0 else 0.0
        loss = -change if change < 0.0 else 0.0

        if self._avg_gain is None or self._avg_loss is None:
            self._deltas_seen += 1
            self._avg_gain = (0.0 if self._avg_gain is None else self._avg_gain) + gain
            self._avg_loss = (0.0 if self._avg_loss is None else self._avg_loss) + loss

            if self._deltas_seen >= self.period:
                self._avg_gain /= self.period
                self._avg_loss /= self.period
                self.value = self._calc_rsi(self._avg_gain, self._avg_loss)
            else:
                self.value = None
        else:
            n = self.period
            self._avg_gain = (self._avg_gain * (n - 1) + gain) / n
            self._avg_loss = (self._avg_loss * (n - 1) + loss) / n
            self.value = self._calc_rsi(self._avg_gain, self._avg_loss)
            self._deltas_seen += 1

        self._prev_price = p
        return self.value

    @staticmethod
    def _calc_rsi(avg_gain: float, avg_loss: float) -> float:
        if avg_loss == 0.0 and avg_gain == 0.0:
            return 50.0
        if avg_loss == 0.0:
            return 100.0
        if avg_gain == 0.0:
            return 0.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

class EMA:
    def __init__(self, period: int):
        if period <= 0:
            raise ValueError("EMA period must be positive")
        self.period = int(period)
        self.mult = 2.0 / (self.period + 1.0)
        self.value: Optional[float] = None

    @property
    def ready(self) -> bool:
        return self.value is not None

    def reset(self):
        self.value = None

    def seed(self, price: float):
        self.value = float(price)

    def update(self, price: float) -> float:
        p = float(price)
        if self.value is None:
            self.value = p
        else:
            self.value = (p - self.value) * self.mult + self.value
        return self.value

class MACD:
    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        if not (fast > 0 and slow > 0 and signal > 0):
            raise ValueError("MACD periods must be positive")
        if fast >= slow:
            raise ValueError("MACD 'fast' must be < 'slow'")
        self.fast = EMA(fast)
        self.slow = EMA(slow)
        self._signal_ema = EMA(signal)
        self.macd: Optional[float] = None
        self.signal: Optional[float] = None
        self.hist: Optional[float] = None

    @property
    def ready(self) -> bool:
        return self.hist is not None

    def reset(self):
        self.fast.reset(); self.slow.reset(); self._signal_ema.reset()
        self.macd = self.signal = self.hist = None

    def seed(self, price: float):
        self.fast.seed(price); self.slow.seed(price)

    def update(self, price: float) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        f = self.fast.update(price)
        s = self.slow.update(price)
        self.macd = f - s if (f is not None and s is not None) else None
        if self.macd is None:
            self.signal = None; self.hist = None
            return self.macd, self.signal, self.hist
        sig = self._signal_ema.update(self.macd)
        self.signal = sig
        self.hist = None if sig is None else (self.macd - sig)
        return self.macd, self.signal, self.hist
