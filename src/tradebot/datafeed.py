"""Veri kaynağı seçici.

FapiFeed   : Binance USDⓈ-M futures (PC/VPS — tam veri)
VisionFeed : data-api.binance.vision spot (GitHub Actions — ABD bloğunu aşar;
             spot fiyat perp'ten birkaç $ sapabilir, analiz için yeterli)
make_feed(): futures erişilemezse otomatik Vision'a düşer.
"""
from __future__ import annotations

import requests
from loguru import logger

from tradebot.exchange.binance_futures import klines_to_df


class VisionFeed:
    BASE = "https://data-api.binance.vision/api/v3"

    def klines(self, symbol: str, interval: str, limit: int = 120):
        r = requests.get(f"{self.BASE}/klines",
                         params={"symbol": symbol, "interval": interval, "limit": limit},
                         timeout=30)
        r.raise_for_status()
        return klines_to_df(r.json())

    def mark_price(self, symbol: str) -> float:
        r = requests.get(f"{self.BASE}/ticker/price", params={"symbol": symbol}, timeout=15)
        r.raise_for_status()
        return float(r.json()["price"])

    def ticker24(self, symbol: str) -> dict:
        r = requests.get(f"{self.BASE}/ticker/24hr", params={"symbol": symbol}, timeout=15)
        r.raise_for_status()
        return r.json()


class FapiFeed:
    def __init__(self):
        from binance.client import Client
        self.c = Client(requests_params={"timeout": 30})  # ping atar; blokta patlar

    def klines(self, symbol: str, interval: str, limit: int = 120):
        return klines_to_df(self.c.futures_klines(symbol=symbol, interval=interval, limit=limit))

    def mark_price(self, symbol: str) -> float:
        return float(self.c.futures_mark_price(symbol=symbol)["markPrice"])

    def ticker24(self, symbol: str) -> dict:
        return self.c.futures_ticker(symbol=symbol)


def make_feed():
    try:
        f = FapiFeed()
        logger.info("veri kaynağı: Binance Futures (fapi)")
        return f
    except Exception as e:  # noqa: BLE001 — ABD bloğu / ağ: spot Vision'a düş
        logger.warning(f"futures erişilemedi ({type(e).__name__}); Vision spot'a geçildi")
        return VisionFeed()
