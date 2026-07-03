"""Senin stratejin: Binance MA7 / MA25 / MA99 kesişimi (15m).

Binance grafiğindeki MA çizgileriyle birebir aynı mantık (SMA):
  * MA7, MA25'i YUKARI keser VE fiyat MA99 üstünde (yukarı trend) -> LONG
  * MA7, MA25'i AŞAĞI keser VE fiyat MA99 altında (aşağı trend)  -> SHORT
  * Açık pozisyon, ters kesişimde kapanır.

MA99 burada büyük trend filtresidir: sadece trend yönünde işlem açar, bu da
işlem sayısını azaltır (az ve sade işlem). Stop/TP'yi RiskManager yönetir.
"""
from __future__ import annotations

import pandas as pd

from tradebot.indicators import sma
from tradebot.models import Position, Side, Signal, SignalType
from tradebot.strategy.base import Strategy


class MaCrossStrategy(Strategy):
    def __init__(self, params: dict | None = None):
        super().__init__(params)
        p = self.params
        self.ma_fast = int(p.get("ma_fast", 7))
        self.ma_mid = int(p.get("ma_mid", 25))
        self.ma_slow = int(p.get("ma_slow", 99))
        self.use_trend_filter = bool(p.get("use_trend_filter", True))  # MA99 filtresi

    def _min_bars(self) -> int:
        return self.ma_slow + 2

    def on_candle(self, df: pd.DataFrame, position: Position | None) -> Signal:
        if len(df) < self._min_bars():
            return Signal(SignalType.HOLD, "yetersiz veri")

        close = df["close"]
        f = sma(close, self.ma_fast)
        m = sma(close, self.ma_mid)
        s = sma(close, self.ma_slow)
        price = float(close.iloc[-1])

        up = f.iloc[-2] <= m.iloc[-2] and f.iloc[-1] > m.iloc[-1]     # MA7 yukarı keser
        down = f.iloc[-2] >= m.iloc[-2] and f.iloc[-1] < m.iloc[-1]   # MA7 aşağı keser
        trend_up = price > float(s.iloc[-1])
        trend_down = price < float(s.iloc[-1])

        # Açık pozisyon: ters kesişimde çık
        if position and position.side == Side.LONG and down:
            return Signal(SignalType.EXIT, "MA7 MA25'i aşağı kesti", price)
        if position and position.side == Side.SHORT and up:
            return Signal(SignalType.EXIT, "MA7 MA25'i yukarı kesti", price)
        if position:
            return Signal(SignalType.HOLD, "pozisyon korunuyor", price)

        # Giriş: kesişim (+ istenirse MA99 trend filtresi)
        if up and (trend_up or not self.use_trend_filter):
            return Signal(SignalType.ENTER_LONG, "MA7↑MA25 + trend↑", price)
        if down and (trend_down or not self.use_trend_filter):
            return Signal(SignalType.ENTER_SHORT, "MA7↓MA25 + trend↓", price)
        return Signal(SignalType.HOLD, "kesişim yok", price)
