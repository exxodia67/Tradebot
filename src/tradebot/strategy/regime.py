"""⭐ Rejim-anahtarlı meta-strateji.

ADX ile piyasa rejimini ölçer ve uygun alt-stratejiye yönlendirir:
  * ADX >= adx_trend  -> trend-takip (TrendStrategy)
  * ADX <= adx_range  -> ortalamaya dönüş (MeanReversionStrategy)
  * arada (no-trade)  -> HOLD (choppy piyasada işlem yapma)

Açık pozisyon varken, pozisyonu AÇAN alt-strateji ile yönetilir (rejim değişse
bile çıkış mantığı tutarlı kalır).
"""
from __future__ import annotations

import pandas as pd

from tradebot.indicators import adx
from tradebot.models import Position, Signal, SignalType
from tradebot.strategy.base import Strategy
from tradebot.strategy.mean_reversion import MeanReversionStrategy
from tradebot.strategy.trend import TrendStrategy


class RegimeStrategy(Strategy):
    def __init__(self, params: dict | None = None):
        super().__init__(params)
        p = self.params
        self.adx_period = int(p.get("adx_period", 14))
        self.adx_trend = float(p.get("adx_trend", 25))
        self.adx_range = float(p.get("adx_range", 20))
        self.trend = TrendStrategy(p.get("trend", {}))
        self.mr = MeanReversionStrategy(p.get("mean_reversion", {}))
        self._active: Strategy | None = None  # açık pozisyonu yöneten alt-strateji

    def _min_bars(self) -> int:
        return max(self.adx_period * 2, self.trend._min_bars(), self.mr._min_bars())

    def on_candle(self, df: pd.DataFrame, position: Position | None) -> Signal:
        if len(df) < self._min_bars():
            return Signal(SignalType.HOLD, "yetersiz veri")

        # Açık pozisyon: onu açan alt-strateji ile yönet
        if position is not None:
            sub = self._active or self.trend  # engine restart edge'i için yedek
            sig = sub.on_candle(df, position)
            if sig.type == SignalType.EXIT:
                self._active = None
            return sig

        # Flat: rejime göre alt-strateji seç
        adx_val = float(adx(df, self.adx_period).iloc[-1])
        if adx_val >= self.adx_trend:
            sub = self.trend
            regime = f"TREND(ADX={adx_val:.0f})"
        elif adx_val <= self.adx_range:
            sub = self.mr
            regime = f"RANGE(ADX={adx_val:.0f})"
        else:
            return Signal(SignalType.HOLD, f"no-trade bölgesi (ADX={adx_val:.0f})")

        sig = sub.on_candle(df, None)
        if sig.is_entry:
            self._active = sub
            sig.reason = f"{regime} | {sig.reason}"
        return sig
