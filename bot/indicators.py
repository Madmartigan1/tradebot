# bot/indicators.py
from __future__ import annotations
from typing import Optional


class RSI:
    """
    Stateful RSI calculator (Wilder’s). Feeds one price at a time via update(price).
    Returns the current RSI value in .value (None until enough data).
    """
    def __init__(self, period: int = 14):
        if period <= 0:
            raise ValueError("RSI period must be positive")
        self.period = period
        self._prev_price: Optional[float] = None
        self._avg_gain: Optional[float] = None
        self._avg_loss: Optional[float] = None
        self._count = 0
        self.value: Optional[float] = None

    def update(self, price: float) -> Optional[float]:
        if self._prev_price is None:
            self._prev_price = price
            self._count = 1
            self.value = None
            return self.value

        change = price - self._prev_price
        gain = max(change, 0.0)
        loss = max(-change, 0.0)

        if self._avg_gain is None or self._avg_loss is None:
            # bootstrap until we have 'period' deltas
            if self._count < self.period:
                self._avg_gain = (0.0 if self._avg_gain is None else self._avg_gain) + gain
                self._avg_loss = (0.0 if self._avg_loss is None else self._avg_loss) + loss
                self._count += 1
                self.value = None
            else:
                # first Wilder averages
                self._avg_gain = ((0.0 if self._avg_gain is None else self._avg_gain) + gain) / self.period
                self._avg_loss = ((0.0 if self._avg_loss is None else self._avg_loss) + loss) / self.period
                self.value = self._calc_rsi(self._avg_gain, self._avg_loss)
        else:
            # Wilder smoothing
            self._avg_gain = (self._avg_gain * (self.period - 1) + gain) / self.period
            self._avg_loss = (self._avg_loss * (self.period - 1) + loss) / self.period
            self.value = self._calc_rsi(self._avg_gain, self._avg_loss)

        self._prev_price = price
        return self.value

    @staticmethod
    def _calc_rsi(avg_gain: float, avg_loss: float) -> float:
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))


class EMA:
    """Simple stateful EMA used by MACD below."""
    def __init__(self, period: int):
        if period <= 0:
            raise ValueError("EMA period must be positive")
        self.period = period
        self.mult = 2.0 / (period + 1.0)
        self.value: Optional[float] = None

    def update(self, price: float) -> float:
        if self.value is None:
            self.value = price
        else:
            self.value = (price - self.value) * self.mult + self.value
        return self.value


class MACD:
    """
    Stateful MACD calculator. Feed one price at a time via update(price).
    Exposes:
      - macd  (fastEMA - slowEMA)
      - signal (EMA of macd)
      - hist  (macd - signal)
    """
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

    def update(self, price: float) -> tuple[Optional[float], Optional[float], Optional[float]]:
        f = self.fast.update(price)
        s = self.slow.update(price)
        self.macd = f - s if (f is not None and s is not None) else None
        if self.macd is None:
            self.signal = None
            self.hist = None
            return self.macd, self.signal, self.hist

        self.signal = self._signal_ema.update(self.macd)
        if self.signal is None:
            self.hist = None
        else:
            self.hist = self.macd - self.signal
        return self.macd, self.signal, self.hist
