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


def supertrend(df: pd.DataFrame, period: int = 10, mult: float = 3.0) -> pd.Series:
    """Supertrend yönü: +1 yukarı, -1 aşağı (TradingView'deki klasik algoritma).

    TV topluluğunun en çok kullanılan trend-takip scriptlerinin (UT Bot,
    TrendMaster vb.) çekirdeği budur: ATR bantlı dinamik destek/direnç.
    """
    hl2 = (df["high"] + df["low"]) / 2
    a = atr(df, period)
    ub = (hl2 + mult * a).to_numpy()
    lb = (hl2 - mult * a).to_numpy()
    close = df["close"].to_numpy()
    n = len(df)
    fub = ub.copy()
    flb = lb.copy()
    dir_ = [0] * n
    for i in range(1, n):
        fub[i] = ub[i] if (ub[i] < fub[i - 1] or close[i - 1] > fub[i - 1]) else fub[i - 1]
        flb[i] = lb[i] if (lb[i] > flb[i - 1] or close[i - 1] < flb[i - 1]) else flb[i - 1]
        if close[i] > fub[i - 1]:
            dir_[i] = 1
        elif close[i] < flb[i - 1]:
            dir_[i] = -1
        else:
            dir_[i] = dir_[i - 1]
    return pd.Series(dir_, index=df.index)


def vwap_daily(df: pd.DataFrame) -> pd.Series:
    """Gün içi (UTC günü) kümülatif VWAP. df: open_time, high, low, close, volume."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    day = pd.to_datetime(df["open_time"]).dt.date
    pv = (tp * df["volume"]).groupby(day).cumsum()
    vv = df["volume"].groupby(day).cumsum()
    return pv / vv.replace(0, pd.NA)


def wavetrend(df: pd.DataFrame, ch: int = 10, avg: int = 21
              ) -> tuple[pd.Series, pd.Series]:
    """WaveTrend osilatörü (LazyBear) — (wt1, wt2). ±60 uç, ±40 aşırı bölge.

    Aşırı bölgeden wt1'in wt2'yi kesmesi klasik dönüş sinyalidir.
    """
    ap = (df["high"] + df["low"] + df["close"]) / 3
    esa = ema(ap, ch)
    d = ema((ap - esa).abs(), ch)
    ci = (ap - esa) / (0.015 * d.replace(0, pd.NA))
    wt1 = ema(ci.fillna(0.0), avg)
    wt2 = sma(wt1, 4)
    return wt1, wt2


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
