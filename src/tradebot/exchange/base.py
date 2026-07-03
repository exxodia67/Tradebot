"""Borsa-bağımsız adaptör arayüzü.

Strateji/risk/engine kodu yalnız bu arayüze bağımlıdır; böylece ileride
başka bir borsa (ör. ccxt veya XM/MT5) bu arayüzü uygulayarak takılabilir.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from tradebot.models import Side


class ExchangeAdapter(ABC):
    """Tüm borsa adaptörlerinin uyması gereken sözleşme."""

    @abstractmethod
    def ping(self) -> bool:
        """Bağlantı/sunucu zamanı kontrolü."""

    @abstractmethod
    def get_balance(self, asset: str = "USDT") -> float:
        """Cüzdandaki kullanılabilir bakiye."""

    @abstractmethod
    def get_klines(self, symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
        """Geçmiş mumlar. Kolonlar: open_time, open, high, low, close, volume."""

    @abstractmethod
    def get_mark_price(self, symbol: str) -> float:
        """Anlık işaret (mark) fiyatı."""

    @abstractmethod
    def set_leverage(self, symbol: str, leverage: int) -> None:
        """Sembol için kaldıracı ayarla."""

    @abstractmethod
    def market_order(self, symbol: str, side: Side, quantity: float) -> dict:
        """Piyasa emri gönder (pozisyon aç/kapat)."""

    @abstractmethod
    def place_stop_orders(
        self,
        symbol: str,
        side: Side,
        quantity: float,
        stop_loss: float | None,
        take_profit: float | None,
    ) -> dict:
        """Pozisyon için SL/TP koruma emirleri (reduce-only)."""

    @abstractmethod
    def cancel_all(self, symbol: str) -> None:
        """Semboldeki tüm açık emirleri iptal et."""

    @abstractmethod
    def get_position(self, symbol: str) -> dict | None:
        """Borsadaki açık pozisyon bilgisi (yoksa None)."""
