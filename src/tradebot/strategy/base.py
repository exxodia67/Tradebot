"""Strateji soyut arayüzü.

Yeni strateji eklemek için bu sınıftan türet, `on_candle` doldur ve
`strategy/__init__.py` içindeki REGISTRY'e ekle. Strateji borsayı/emirleri
bilmez — sadece mum verisine bakıp bir Signal üretir. Bu sayede aynı strateji
hem canlı engine'de hem backtester'da çalışır.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from tradebot.models import Position, Signal


class Strategy(ABC):
    def __init__(self, params: dict | None = None):
        self.params = params or {}

    @abstractmethod
    def on_candle(self, df: pd.DataFrame, position: Position | None) -> Signal:
        """Kapanan mum verisi (kronolojik) ile karar üret.

        Args:
            df: open_time, open, high, low, close, volume kolonlu DataFrame.
            position: Açık pozisyon (varsa) — çıkış kararı için.
        Returns:
            Signal (ENTER_LONG / ENTER_SHORT / EXIT / HOLD).
        """

    @property
    def name(self) -> str:
        return self.__class__.__name__
