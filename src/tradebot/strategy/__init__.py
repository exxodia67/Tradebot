"""Tak-çıkar strateji modülleri."""
from __future__ import annotations

from tradebot.strategy.base import Strategy
from tradebot.strategy.ema_rsi import EmaRsiStrategy
from tradebot.strategy.ma_cross import MaCrossStrategy
from tradebot.strategy.mean_reversion import MeanReversionStrategy
from tradebot.strategy.mr_trend import MrTrendStrategy
from tradebot.strategy.regime import RegimeStrategy
from tradebot.strategy.trend import TrendStrategy

# Strateji kayıt defteri: config.yaml'daki `strategy.name` buradan çözülür.
REGISTRY: dict[str, type[Strategy]] = {
    "ema_rsi": EmaRsiStrategy,           # ilk basit örnek (baseline)
    "trend": TrendStrategy,              # EMA + MACD (tek başına da kullanılabilir)
    "mean_reversion": MeanReversionStrategy,
    "mr_trend": MrTrendStrategy,         # ⭐ MR + üst-trend filtresi (confluence)
    "regime": RegimeStrategy,            # ADX ile trend/range anahtarlama
    "ma_cross": MaCrossStrategy,         # senin stratejin: MA7/25/99 (15m)
}


def build_strategy(name: str, params: dict) -> Strategy:
    if name not in REGISTRY:
        raise ValueError(
            f"Bilinmeyen strateji '{name}'. Mevcut: {list(REGISTRY)}"
        )
    return REGISTRY[name](params)
