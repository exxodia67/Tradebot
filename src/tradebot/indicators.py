"""Hafif teknik indikatörler (pandas tabanlı, ek bağımlılık yok).

pandas-ta gibi paketler numpy sürüm çakışmaları yaşatabildiği için
EMA/RSI burada elle hesaplanır — daha az bağımlılık, daha sağlam kurulum.
"""
from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    """Üssel hareketli ortalama."""
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """Basit hareketli ortalama (Binance'deki MA çizgileri SMA'dır)."""
    return series.rolling(period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI (0-100)."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    # avg_loss == 0 -> rs = +inf -> out = 100 (sadece yükseliş). Bu doğru davranış.
    # avg_gain == avg_loss == 0 (düz) veya warmup -> NaN -> nötr 50.
    rs = avg_gain / avg_loss
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0)


def macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD çizgisi, sinyal çizgisi ve histogram. hist>0 = boğa momentumu."""
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def bollinger(
    series: pd.Series, period: int = 20, std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger bantları: (orta=SMA, üst, alt)."""
    mid = series.rolling(period).mean()
    dev = series.rolling(period).std()
    return mid, mid + std * dev, mid - std * dev


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range (Wilder). Volatilite ölçüsü — dinamik stop için.

    df: high, low, close kolonlarını içermeli.
    """
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    true_range = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index — trend gücü (rejim filtresi için).

    ADX > 25 genelde "gerçek trend", < 20 "yatay/range" kabul edilir.
    df: high, low, close kolonlarını içermeli.
    """
    high, low, close = df["high"], df["low"], df["close"]
    up = high.diff()
    down = -low.diff()
    plus_dm = ((up > down) & (up > 0)) * up
    minus_dm = ((down > up) & (down > 0)) * down

    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr_ = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, pd.NA)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().fillna(0.0)
