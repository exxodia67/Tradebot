"""Öğrenme motoru testleri — çevrimdışı, sentetik veri."""
from collections import namedtuple

import numpy as np
import pandas as pd

from tradebot.learner import add_higher_trend, prep, simulate, walk_forward

Bar = namedtuple("Bar", "open_time open high low close volume close_time")


def make_df(n=400, step=0.1):
    t = np.arange(n, dtype=float)
    close = 100 + t * step + np.sin(t / 10) * 0.8
    ot = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({
        "open_time": ot, "open": close - 0.1, "high": close + 0.5,
        "low": close - 0.5, "close": close, "volume": np.full(n, 1000.0),
        "close_time": ot + pd.Timedelta(minutes=15),
    })


def test_simulate_pessimistic_same_bar():
    """Aynı barda hem stop hem hedef vurulursa STOP sayılmalı (kötümser)."""
    ot = pd.Timestamp("2026-01-01", tz="UTC")
    rows = [
        Bar(ot, 100, 100.5, 99.5, 100, 1000, ot),
        Bar(ot, 100, 105.0, 95.0, 100, 1000, ot),  # dev bar: ikisini de vurur
    ]
    outcome, pnl, _ = simulate(rows, 0, "LONG", 100.0, 1.0, 2.0, 10)
    assert outcome == "STOP"
    assert pnl < 0


def test_simulate_target_hit():
    ot = pd.Timestamp("2026-01-01", tz="UTC")
    rows = [
        Bar(ot, 100, 100.5, 99.5, 100, 1000, ot),
        Bar(ot, 100, 103.0, 99.6, 102, 1000, ot),  # hedef 102, stop 99'a değmez
    ]
    outcome, pnl, _ = simulate(rows, 0, "LONG", 100.0, 1.0, 2.0, 10)
    assert outcome == "HEDEF"
    assert pnl > 0


def test_walk_forward_smoke():
    """Düzenli yukarı trendde walk-forward çalışır ve LONG işlemler üretir."""
    df = make_df(400)
    d = add_higher_trend(prep(df, 96), prep(df.copy(), 24))
    trades = walk_forward(d, max_hold=20)
    assert isinstance(trades, list)
    assert len(trades) > 0            # trend var, P1 tetiklenmeli
    # MR (sekme) pattern'i bilerek trend filtresiz; trend patternleri LONG olmalı
    trend_trades = [t for t in trades if t.pattern != "P9_SEKME_MR"]
    assert all(t.side == "LONG" for t in trend_trades)
    assert all(np.isfinite(t.pnl_pct) for t in trades)
