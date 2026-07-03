"""Strateji + indikatör testleri (ağ yok, sentetik veri)."""
import numpy as np
import pandas as pd

from tradebot.indicators import ema, rsi
from tradebot.models import SignalType
from tradebot.strategy.ema_rsi import EmaRsiStrategy


def _df(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame({
        "open_time": pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC"),
        "open": closes, "high": [c * 1.001 for c in closes],
        "low": [c * 0.999 for c in closes], "close": closes,
        "volume": [1.0] * n,
    })


def test_ema_responds_faster_than_slow():
    s = pd.Series(np.linspace(100, 110, 50))
    assert ema(s, 5).iloc[-1] > ema(s, 20).iloc[-1]


def test_rsi_bounds():
    s = pd.Series(np.linspace(100, 200, 100))  # sürekli artış -> RSI yüksek
    r = rsi(s, 14)
    assert (r >= 0).all() and (r <= 100).all()
    assert r.iloc[-1] > 60


def test_strategy_emits_long_on_upcross():
    # Önce düşüş (fast<slow), sonra güçlü yükseliş -> yukarı kesişim beklenir
    closes = [100 - i * 0.5 for i in range(30)] + [85 + i * 1.5 for i in range(30)]
    strat = EmaRsiStrategy({"ema_fast": 9, "ema_slow": 21, "rsi_long_max": 90})
    seen = set()
    for i in range(25, len(closes)):
        sig = strat.on_candle(_df(closes[: i + 1]), None)
        seen.add(sig.type)
    assert SignalType.ENTER_LONG in seen


def test_insufficient_data_holds():
    strat = EmaRsiStrategy()
    sig = strat.on_candle(_df([100, 101, 102]), None)
    assert sig.type == SignalType.HOLD


def test_registry_has_all_strategies():
    from tradebot.strategy import REGISTRY, build_strategy

    for name in ("ema_rsi", "trend", "mean_reversion", "regime"):
        assert name in REGISTRY
        assert build_strategy(name, {}) is not None


def test_strategies_run_without_error_on_trend_data():
    """Her strateji yükseliş trendi verisinde çökmeden sinyal üretmeli."""
    from tradebot.strategy import build_strategy

    closes = [100 + i * 0.3 + (i % 5) for i in range(120)]  # trend + gürültü
    df = _df(closes)
    for name in ("trend", "mean_reversion", "regime"):
        strat = build_strategy(name, {})
        types = {strat.on_candle(df.iloc[: i + 1], None).type for i in range(60, len(df))}
        assert types  # en az bir karar (HOLD dahil) üretildi


def test_regime_holds_when_insufficient_data():
    from tradebot.strategy import build_strategy

    strat = build_strategy("regime", {})
    sig = strat.on_candle(_df([100, 101, 102, 103]), None)
    assert sig.type == SignalType.HOLD
