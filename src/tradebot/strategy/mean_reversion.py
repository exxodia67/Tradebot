"""Ortalamaya dönüş stratejisi: RSI + Bollinger bantları.

Sadece yatay/range rejiminde (RegimeStrategy tarafından ADX ile seçilir)
çalışması amaçlanır. Fiyat alt banda + RSI aşırı satıma düşünce long; üst banda
+ aşırı alıma çıkınca short. Çıkış orta banda (ortalamaya) dönüşte.
"""
from __future__ import annotations

import pandas as pd

from tradebot.indicators import bollinger, rsi
from tradebot.models import Position, Side, Signal, SignalType
from tradebot.strategy.base import Strategy


class MeanReversionStrategy(Strategy):
    def __init__(self, params: dict | None = None):
        super().__init__(params)
        p = self.params
        self.rsi_period = int(p.get("rsi_period", 14))
        self.rsi_oversold = float(p.get("rsi_oversold", 30))
        self.rsi_overbought = float(p.get("rsi_overbought", 70))
        self.bb_period = int(p.get("bb_period", 20))
        self.bb_std = float(p.get("bb_std", 2.0))

    def _min_bars(self) -> int:
        return max(self.bb_period, self.rsi_period) + 2

    def on_candle(self, df: pd.DataFrame, position: Position | None) -> Signal:
        if len(df) < self._min_bars():
            return Signal(SignalType.HOLD, "yetersiz veri")

        close = df["close"]
        r = rsi(close, self.rsi_period)
        mid, upper, lower = bollinger(close, self.bb_period, self.bb_std)
        price = float(close.iloc[-1])
        rv = float(r.iloc[-1])
        m = float(mid.iloc[-1])

        # Açık pozisyon: ortalamaya (orta band) dönüşte çık
        if position and position.side == Side.LONG and price >= m:
            return Signal(SignalType.EXIT, "mr: ortalamaya dönüş", price)
        if position and position.side == Side.SHORT and price <= m:
            return Signal(SignalType.EXIT, "mr: ortalamaya dönüş", price)
        if position:
            return Signal(SignalType.HOLD, "mr: pozisyon korunuyor", price)

        # Giriş: bant + RSI aşırılığı
        if price <= float(lower.iloc[-1]) and rv < self.rsi_oversold:
            return Signal(SignalType.ENTER_LONG, f"mr: alt band & RSI={rv:.0f}", price)
        if price >= float(upper.iloc[-1]) and rv > self.rsi_overbought:
            return Signal(SignalType.ENTER_SHORT, f"mr: üst band & RSI={rv:.0f}", price)
        return Signal(SignalType.HOLD, "mr: sinyal yok", price)
