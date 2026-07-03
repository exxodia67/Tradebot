"""Örnek strateji: EMA kesişimi + RSI filtresi.

Mantık (yalın bir başlangıç — kâr garantisi DEĞİL):
  * Hızlı EMA, yavaş EMA'yı YUKARI keser ve RSI aşırı-alımda değilse -> LONG
  * Hızlı EMA, yavaş EMA'yı AŞAĞI keser ve RSI aşırı-satımda değilse -> SHORT
  * Açık pozisyon ters kesişimde kapanır (EXIT). SL/TP'yi RiskManager yönetir.
"""
from __future__ import annotations

import pandas as pd

from tradebot.indicators import ema, rsi
from tradebot.models import Position, Side, Signal, SignalType
from tradebot.strategy.base import Strategy


class EmaRsiStrategy(Strategy):
    def __init__(self, params: dict | None = None):
        super().__init__(params)
        self.ema_fast = int(self.params.get("ema_fast", 9))
        self.ema_slow = int(self.params.get("ema_slow", 21))
        self.rsi_period = int(self.params.get("rsi_period", 14))
        self.rsi_long_max = float(self.params.get("rsi_long_max", 70))
        self.rsi_short_min = float(self.params.get("rsi_short_min", 30))

    def _enrich(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["ema_fast"] = ema(out["close"], self.ema_fast)
        out["ema_slow"] = ema(out["close"], self.ema_slow)
        out["rsi"] = rsi(out["close"], self.rsi_period)
        return out

    def on_candle(self, df: pd.DataFrame, position: Position | None) -> Signal:
        if len(df) < self.ema_slow + 2:
            return Signal(SignalType.HOLD, reason="yetersiz veri")

        d = self._enrich(df)
        prev, last = d.iloc[-2], d.iloc[-1]
        price = float(last["close"])

        crossed_up = prev["ema_fast"] <= prev["ema_slow"] and last["ema_fast"] > last["ema_slow"]
        crossed_down = prev["ema_fast"] >= prev["ema_slow"] and last["ema_fast"] < last["ema_slow"]

        # Açık pozisyon varsa: ters kesişimde çık
        if position and position.side == Side.LONG and crossed_down:
            return Signal(SignalType.EXIT, "EMA aşağı kesişim", price)
        if position and position.side == Side.SHORT and crossed_up:
            return Signal(SignalType.EXIT, "EMA yukarı kesişim", price)
        if position:
            return Signal(SignalType.HOLD, "pozisyon korunuyor", price)

        # Pozisyon yoksa: giriş ara
        if crossed_up and last["rsi"] < self.rsi_long_max:
            return Signal(SignalType.ENTER_LONG, f"EMA↑ & RSI={last['rsi']:.0f}", price)
        if crossed_down and last["rsi"] > self.rsi_short_min:
            return Signal(SignalType.ENTER_SHORT, f"EMA↓ & RSI={last['rsi']:.0f}", price)

        return Signal(SignalType.HOLD, "sinyal yok", price)
