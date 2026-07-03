"""Journal (co-pilot günlüğü) testi — çevrimdışı, geçici DB."""
from tradebot.journal import Journal


def test_journal_add_close_summary(tmp_path):
    j = Journal(tmp_path / "j.db")
    # İki kurulum: biri kazanç, biri kayıp
    a1 = j.add("ETHUSDT", "LONG", 1600, 1584, 1632, 28, "test1")
    a2 = j.add("ETHUSDT", "SHORT", 1600, 1616, 1568, 30, "test2")
    assert isinstance(a1, int) and a2 == a1 + 1

    s0 = j.summary()
    assert s0["kapanan"] == 0  # henüz kapanmadı

    j.close(a1, "HEDEF", pnl_pct=2.0)
    j.close(a2, "STOP", pnl_pct=-1.0)

    s = j.summary()
    assert s["kapanan"] == 2
    assert s["win_rate"] == 50.0
    assert s["toplam_pnl_pct"] == 1.0
