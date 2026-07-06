"""Co-pilot işlem günlüğü (journal) + öğrenme raporu.

Her kaliteli kurulum uyarısını, GİRİŞ ANINDAKİ ÖZELLİK FOTOĞRAFINI (RSI, hacim
oranı, dirence/desteğe mesafe, MA ayrımı, saat) ve nasıl sonuçlandığını kaydeder.
"Öğrenme"nin temeli budur: veri birikince hangi koşulun kazandırdığını ararız.
Kendi başına SQLite dosyası tutar (copilot_journal.db).

Rapor:  python -m tradebot.journal
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from tradebot.config import ROOT, STATE_DIR

# Giriş anında saklanan ek özellikler (sonradan öğrenme analizi için)
_FEATURES = ("rsi", "vol_ratio", "room_atr", "sep_pct", "hour")


class Journal:
    def __init__(self, path: Path | str | None = None):
        if path is None:
            # Sabit ev-klasörü: bot hangi klasörden çalışırsa çalışsın TEK karne.
            path = STATE_DIR / "copilot_journal.db"
            old = ROOT / "copilot_journal.db"
            if not path.exists() and old.exists():
                try:
                    shutil.copy2(old, path)   # eski kurulumdaki kayıtları taşı
                except Exception:  # noqa: BLE001
                    path = old                # taşınamazsa eskisiyle devam
        self.path = str(path)
        con = sqlite3.connect(self.path)
        con.execute(
            """CREATE TABLE IF NOT EXISTS alerts(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, symbol TEXT, side TEXT,
                entry REAL, stop REAL, target REAL,
                adx REAL, reason TEXT,
                rsi REAL, vol_ratio REAL, room_atr REAL, sep_pct REAL, hour INTEGER,
                outcome TEXT, pnl_pct REAL, closed_at TEXT)"""
        )
        # Eski DB'lere eksik kolonları ekle (geriye dönük uyum)
        have = {r[1] for r in con.execute("PRAGMA table_info(alerts)").fetchall()}
        for col in _FEATURES:
            if col not in have:
                typ = "INTEGER" if col == "hour" else "REAL"
                con.execute(f"ALTER TABLE alerts ADD COLUMN {col} {typ}")
        con.commit()
        con.close()

    def add(self, symbol, side, entry, stop, target, adx, reason, **features) -> int:
        """features: rsi, vol_ratio, room_atr, sep_pct, hour (hepsi opsiyonel)."""
        con = sqlite3.connect(self.path)
        cur = con.execute(
            "INSERT INTO alerts(ts,symbol,side,entry,stop,target,adx,reason,"
            "rsi,vol_ratio,room_atr,sep_pct,hour) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), symbol, side,
             entry, stop, target, adx, reason,
             features.get("rsi"), features.get("vol_ratio"),
             features.get("room_atr"), features.get("sep_pct"), features.get("hour")),
        )
        con.commit()
        rid = cur.lastrowid
        con.close()
        return rid

    def close(self, alert_id: int, outcome: str, pnl_pct: float) -> None:
        con = sqlite3.connect(self.path)
        con.execute(
            "UPDATE alerts SET outcome=?, pnl_pct=?, closed_at=? WHERE id=?",
            (outcome, pnl_pct, datetime.now(timezone.utc).isoformat(), alert_id),
        )
        con.commit()
        con.close()

    def summary(self) -> dict:
        con = sqlite3.connect(self.path)
        rows = con.execute(
            "SELECT outcome, pnl_pct FROM alerts WHERE outcome IS NOT NULL"
        ).fetchall()
        con.close()
        n = len(rows)
        if n == 0:
            return {"kapanan": 0, "win_rate": 0.0, "ort_pnl_pct": 0.0, "toplam_pnl_pct": 0.0}
        pnls = [p for _, p in rows if p is not None]
        return {
            "kapanan": n,
            "win_rate": round(sum(1 for p in pnls if p > 0) / n * 100, 1),
            "ort_pnl_pct": round(sum(pnls) / len(pnls), 3) if pnls else 0.0,
            "toplam_pnl_pct": round(sum(pnls), 2),
        }

    def last_trades(self, n: int = 8) -> list[dict]:
        """Son n kayıt (açık + kapalı), yeniden eskiye."""
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        con.close()
        return [dict(r) for r in rows]

    # ---- öğrenme analizi ------------------------------------------------
    def closed_rows(self) -> list[dict]:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM alerts WHERE outcome IS NOT NULL AND pnl_pct IS NOT NULL"
        ).fetchall()
        con.close()
        return [dict(r) for r in rows]

    def learn_report(self, min_trades: int = 30) -> str:
        """Kapanmış işlemleri koşullara göre kırıp win-rate/PnL döker.

        Az veriyle istatistik yanıltıcı olur; min_trades altındaysa dürüstçe uyarır.
        """
        rows = self.closed_rows()
        n = len(rows)
        out = [f"=== ÖĞRENME RAPORU ({n} kapanmış işlem) ==="]
        s = self.summary()
        out.append(f"Genel: win %{s['win_rate']}, ort %{s['ort_pnl_pct']}, "
                   f"toplam %{s['toplam_pnl_pct']}")
        if n == 0:
            out.append("Henüz kapanmış işlem yok — copilot çalışıp kurulumlar sonuçlanınca dolacak.")
            return "\n".join(out)
        if n < min_trades:
            out.append(f"\n[!] Sadece {n} işlem var. İstatistiksel çıkarım için ~{min_trades}+ gerekir.")
            out.append("   Aşağıdakiler FİKİR verir ama KANIT değildir (küçük örnek yanıltır):")

        def bucket(rows, keyfn, label):
            groups: dict = {}
            for r in rows:
                k = keyfn(r)
                if k is None:
                    continue
                groups.setdefault(k, []).append(r)
            lines = [f"\n[{label}]"]
            for k in sorted(groups):
                g = groups[k]
                wins = sum(1 for r in g if r["pnl_pct"] > 0)
                avg = sum(r["pnl_pct"] for r in g) / len(g)
                lines.append(f"  {k:<14} n={len(g):<3} win%={wins / len(g) * 100:>5.0f}  "
                             f"ortPnL%={avg:+.2f}")
            return lines

        def adx_key(r):
            v = r["adx"]
            if v is None:
                return None
            return "ADX<20" if v < 20 else "ADX20-30" if v < 30 else "ADX30+"

        def vol_key(r):
            v = r["vol_ratio"]
            if v is None:
                return None
            return "vol<1.0x" if v < 1.0 else "vol1.0-1.5x" if v < 1.5 else "vol1.5x+"

        out += bucket(rows, lambda r: r["side"], "Yön")
        out += bucket(rows, adx_key, "ADX kovası")
        out += bucket(rows, vol_key, "Hacim oranı")
        out += bucket(rows, lambda r: f"{r['hour']:02d}:00 UTC" if r["hour"] is not None else None,
                      "Saat (UTC)")
        return "\n".join(out)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    print(Journal().learn_report())


if __name__ == "__main__":
    main()
