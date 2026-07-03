"""Trend-takip stratejisi: EMA kesişimi + MACD onayı.

Sadece güçlü trend rejiminde (RegimeStrategy tarafından ADX ile seçilir)
çalışması amaçlanır. MACD histogramı, EMA kesişimini momentumla doğrular ve
zayıf/sahte kesişimleri eler.
"""
from __future__ import annotations

import pandas as pd

from tradebot.indicators import ema, macd
from tradebot.models import Position, Side, Signal, SignalType
from tradebot.strategy.base import Strategy


class TrendStrategy(Strategy):
    def __init__(self, params: dict | None = None):
        super().__init__(params)
        p = self.params
        self.ema_fast = int(p.get("ema_fast", 9))
        self.ema_slow = int(p.get("ema_slow", 21))
        self.macd_fast = int(p.get("macd_fast", 12))
        self.macd_slow = int(p.get("macd_slow", 26))
        self.macd_signal = int(p.get("macd_signal", 9))

    def _min_bars(self) -> int:
        return max(self.ema_slow, self.macd_slow + self.macd_signal) + 2

    def on_candle(self, df: pd.DataFrame, position: Position | None) -> Signal:
        if len(df) < self._min_bars():
            return Signal(SignalType.HOLD, "yetersiz veri")

        close = df["close"]
        ef = ema(close, self.ema_fast)
        es = ema(close, self.ema_slow)
        _, _, hist = macd(close, self.macd_fast, self.macd_slow, self.macd_signal)
        price = float(close.iloc[-1])

        up = ef.iloc[-2] <= es.iloc[-2] and ef.iloc[-1] > es.iloc[-1]
        down = ef.iloc[-2] >= es.iloc[-2] and ef.iloc[-1] < es.iloc[-1]
        h = float(hist.iloc[-1])

        # Açık pozisyon: ters kesişimde çık
        if position and position.side == Side.LONG and down:
            return Signal(SignalType.EXIT, "trend: EMA aşağı kesişim", price)
        if position and position.side == Side.SHORT and up:
            return Signal(SignalType.EXIT, "trend: EMA yukarı kesişim", price)
        if position:
            return Signal(SignalType.HOLD, "trend: pozisyon korunuyor", price)

        # Giriş: kesişim + MACD onayı
        if up and h > 0:
            return Signal(SignalType.ENTER_LONG, f"trend: EMA↑ & MACD+ ({h:.2f})", price)
        if down and h < 0:
            return Signal(SignalType.ENTER_SHORT, f"trend: EMA↓ & MACD- ({h:.2f})", price)
        return Signal(SignalType.HOLD, "trend: sinyal yok", price)
