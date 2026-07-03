"""Birleşik strateji: Mean-Reversion + üst-trend filtresi (confluence).

Fikir: mean-reversion tek başına "düşen bıçağı yakalayabilir". Büyük trend
yönünü bir filtre olarak ekleriz — yalnız trend yönündeki dip/tepe alınır:
  * Uzun EMA üstünde (yukarı trend) -> sadece alt-band LONG'ları al
  * Uzun EMA altında (aşağı trend)  -> sadece üst-band SHORT'ları al

Böylece trend ve mean-reversion ZIT değil, TAMAMLAYICI kullanılır:
trend yönü "ne tarafa", mean-reversion "ne zaman" girileceğini söyler.
"""
from __future__ import annotations

import pandas as pd

from tradebot.indicators import bollinger, ema, rsi
from tradebot.models import Position, Side, Signal, SignalType
from tradebot.strategy.base import Strategy


class MrTrendStrategy(Strategy):
    def __init__(self, params: dict | None = None):
        super().__init__(params)
        p = self.params
        self.rsi_period = int(p.get("rsi_period", 14))
        self.rsi_oversold = float(p.get("rsi_oversold", 35))
        self.rsi_overbought = float(p.get("rsi_overbought", 65))
        self.bb_period = int(p.get("bb_period", 20))
        self.bb_std = float(p.get("bb_std", 2.0))
        self.trend_ema = int(p.get("trend_ema", 200))  # büyük trend filtresi

    def _min_bars(self) -> int:
        return max(self.trend_ema, self.bb_period, self.rsi_period) + 2

    def on_candle(self, df: pd.DataFrame, position: Position | None) -> Signal:
        if len(df) < self._min_bars():
            return Signal(SignalType.HOLD, "yetersiz veri")

        close = df["close"]
        r = rsi(close, self.rsi_period)
        mid, upper, lower = bollinger(close, self.bb_period, self.bb_std)
        trend = ema(close, self.trend_ema)
        price = float(close.iloc[-1])
        rv = float(r.iloc[-1])
        m = float(mid.iloc[-1])
        trend_up = price > float(trend.iloc[-1])

        # Açık pozisyon: ortalamaya (orta band) dönüşte çık
        if position and position.side == Side.LONG and price >= m:
            return Signal(SignalType.EXIT, "mr: ortalamaya dönüş", price)
        if position and position.side == Side.SHORT and price <= m:
            return Signal(SignalType.EXIT, "mr: ortalamaya dönüş", price)
        if position:
            return Signal(SignalType.HOLD, "mr: pozisyon korunuyor", price)

        # Giriş: bant + RSI aşırılığı + TREND YÖNÜ onayı
        if trend_up and price <= float(lower.iloc[-1]) and rv < self.rsi_oversold:
            return Signal(SignalType.ENTER_LONG, f"trend↑ + alt band & RSI={rv:.0f}", price)
        if (not trend_up) and price >= float(upper.iloc[-1]) and rv > self.rsi_overbought:
            return Signal(SignalType.ENTER_SHORT, f"trend↓ + üst band & RSI={rv:.0f}", price)
        return Signal(SignalType.HOLD, "mr+trend: sinyal yok", price)
