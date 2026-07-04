"""Sürekli Öğrenme Daemon'u — bu PC arka planda öğrenir, Telegram'a rapor atar.

Ne yapar:
  * TAM ÖĞRENME (başlangıçta + 6 saatte bir, arka plan thread):
    90 günlük walk-forward, 4 zaman dilimi (5m/15m/1h/4h), 10 pattern.
    En yüksek win oranlı kombinasyonlar (pattern+yön+ADX+hacim) çıkarılır,
    ogrenme_ozet.md dosyasına yazılır.
  * HER 10 DK: Telegram'a rapor — rejim (1h/4h), güncel en iyi kurallar,
    son 48 saatte bu kuralların YAKALADIĞI işlemler (simülasyon, komisyon
    dahil, kötümser sayım) 5x etkileriyle.

ÖNEMLİ: Telegram'a SADECE MESAJ GÖNDERİR — komut DİNLEMEZ (getUpdates yok).
Bu yüzden öbür PC'deki telegram_bot ile ÇAKIŞMAZ, ikisi aynı anda çalışır.

Kullanım:  python -m tradebot.ogrenme_daemon   (veya 10-Ogrenme-Daemon.bat)
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import requests
from loguru import logger

from tradebot.config import ROOT, Secrets
from tradebot.datafeed import make_feed
from tradebot.exchange.binance_futures import fetch_mainnet_klines
from tradebot.indicators import adx, rsi, sma
from tradebot.learner import (_stats, add_higher_trend, adx_b, prep, vol_b,
                              walk_forward)

CHAT_FILE = ROOT / "telegram_chat.json"
OZET_MD = ROOT / "ogrenme_ozet.md"
LEARN_EVERY_H = 6      # tam öğrenme aralığı (saat)
REPORT_SEC = 600       # rapor aralığı (10 dk)

# (giriş TF, trend TF, gün, S/R penceresi, max_hold, taze-bar limiti)
LEARN_PLANS = (
    ("5m",  "15m", 45, 288, 96, 900),
    ("15m", "1h",  90, 96,  96, 900),
    ("1h",  "1d",  90, 24,  72, 900),
    ("4h",  "1d",  240, 42, 42, 600),
)


class OgrenmeDaemon:
    def __init__(self, symbol: str = "ETHUSDT", bot=None):
        self.symbol = symbol
        self.bot = bot          # telegram_bot içinde gömülü mod: açık işlemleri de raporlar
        if bot is not None:
            self.token = bot.token
        else:
            self.token = Secrets().telegram_bot_token
            if not self.token:
                print("HATA: .env içinde TELEGRAM_BOT_TOKEN yok.")
                sys.exit(1)
        self.chat_id = None
        if CHAT_FILE.exists():
            try:
                self.chat_id = json.loads(CHAT_FILE.read_text())["chat_id"]
            except Exception:  # noqa: BLE001
                pass
        if self.chat_id is None and bot is None:
            print("HATA: telegram_chat.json yok — önce bota /start yazılmış olmalı.")
            sys.exit(1)
        self.feed = bot.copilot.feed if bot is not None else make_feed()
        self.best: dict[str, list[str]] = {}     # tf -> en iyi kombo satırları
        self.last_learn: datetime | None = None
        self.learning = False
        self.learn_err: str | None = None

    # ---- telegram (sadece gönderme — dinleme YOK, çakışma YOK) ------------
    def send(self, text: str) -> None:
        if self.chat_id is None:      # gömülü modda /start henüz gelmemiş olabilir
            if self.bot is not None and self.bot.chat_id is not None:
                self.chat_id = self.bot.chat_id
            else:
                return
        for i in range(0, len(text), 3900):
            try:
                requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                              json={"chat_id": self.chat_id, "text": text[i:i + 3900]},
                              timeout=35)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"telegram gönderilemedi: {e}")

    # ---- tam öğrenme (ağır — thread'de koşar) ------------------------------
    def full_learn(self) -> None:
        self.learning = True
        self.learn_err = None
        try:
            t0 = time.time()
            md = [f"# Öğrenme Özeti — {self.symbol}",
                  f"Üretildi: {datetime.now():%Y-%m-%d %H:%M} | walk-forward, "
                  f"komisyon dahil, aynı-bar çakışması=STOP (kötümser)", ""]
            for etf, ttf, days, sr_win, max_hold, _ in LEARN_PLANS:
                logger.info(f"öğrenme: {etf} ({days} gün) ...")
                edf = fetch_mainnet_klines(self.symbol, etf, days)
                tdf = fetch_mainnet_klines(self.symbol, ttf,
                                           365 if ttf == "1d" else days)
                d = add_higher_trend(prep(edf, sr_win), prep(tdf, 24))
                trades = walk_forward(d, max_hold=max_hold)
                n, w, a = _stats(trades)
                md.append(f"\n## {etf} (filtre {ttf}) — {n} işlem, "
                          f"win %{w:.1f}, ort %{a:+.3f}")
                # en iyi kombolar: win% sırası, sadece ort>0 ve n>=15
                groups: dict = {}
                for t in trades:
                    groups.setdefault((t.pattern, t.side, adx_b(t), vol_b(t)),
                                      []).append(t)
                scored = [(k, *_stats(g)) for k, g in groups.items() if len(g) >= 15]
                scored = [s for s in scored if s[3] > 0]        # ort PnL > 0
                scored.sort(key=lambda x: x[2], reverse=True)   # win% sırası
                lines = []
                for k, kn, kw, ka in scored[:5]:
                    lines.append(f"{k[0]} {k[1]} {k[2]} {k[3]} · "
                                 f"n={kn} win%{kw:.0f} ort{ka:+.2f}%")
                self.best[etf] = lines
                md += ["  " + ln for ln in lines] or ["  (kârlı kombo yok)"]
            OZET_MD.write_text("\n".join(md), encoding="utf-8")
            self.last_learn = datetime.now(timezone.utc)
            logger.info(f"tam öğrenme bitti ({time.time() - t0:.0f}s)")
        except Exception as e:  # noqa: BLE001
            self.learn_err = f"{type(e).__name__}: {e}"
            logger.warning(f"öğrenme hatası: {self.learn_err}")
        finally:
            self.learning = False

    # ---- son 48 saatte kurallar ne yakaladı --------------------------------
    def recent_catches(self, hours: int = 48) -> list[str]:
        out: list[str] = []
        cut = datetime.now(timezone.utc) - timedelta(hours=hours)
        for etf, ttf, _days, sr_win, max_hold, limit in LEARN_PLANS:
            if etf == "5m":     # 5m her testte zararlı çıktı — rapora koymuyoruz
                continue
            try:
                edf = self.feed.klines(self.symbol, etf, limit)
                tdf = self.feed.klines(self.symbol, ttf, 400)
                d = add_higher_trend(prep(edf, sr_win), prep(tdf, 24))
                trades = [t for t in walk_forward(d, max_hold=max_hold)
                          if t.ts.to_pydatetime() >= cut]
                trades.sort(key=lambda t: t.pnl_pct, reverse=True)
                for t in trades[:3]:
                    ts_tr = (t.ts.to_pydatetime() + timedelta(hours=3))
                    out.append(f"[{etf}] {t.pattern} {t.side} "
                               f"{ts_tr:%d.%m %H:%M}TR → {t.outcome} "
                               f"{t.pnl_pct:+.2f}% (5x {t.pnl_pct * 5:+.1f}%)")
            except Exception as e:  # noqa: BLE001
                out.append(f"[{etf}] tarama hatası: {e}")
        return out

    # ---- 10 dk raporu -------------------------------------------------------
    def rapor(self) -> str:
        saat = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%H:%M")
        lines = [f"🧠 Öğrenme raporu {saat} TR — {self.symbol}"]
        price = None
        try:
            price = self.feed.mark_price(self.symbol)
            tk = self.feed.ticker24(self.symbol)
            lines[0] += (f" {price:.2f} "
                         f"(24s %{float(tk['priceChangePercent']):+.1f})")
            for tf in ("1h", "4h"):
                d = self.feed.klines(self.symbol, tf, 120)
                m7, m25, m99 = (sma(d["close"], q).iloc[-1] for q in (7, 25, 99))
                a = float(adx(d, 14).iloc[-1])
                r = float(rsi(d["close"], 14).iloc[-1])
                diz = "⬆️" if m7 > m25 > m99 else "⬇️" if m7 < m25 < m99 else "↔️"
                lines.append(f"{tf} {diz} ADX{a:.0f} RSI{r:.0f}")
        except Exception as e:  # noqa: BLE001
            lines.append(f"rejim okunamadı: {e}")

        # gömülü mod: botun açık kâğıt işlemleri de bu rapora girer
        if self.bot is not None and price is not None:
            aktif = getattr(self.bot, "_active", {}) or {}
            if aktif:
                for tf, (s, _aid, _ats) in aktif.items():
                    chg = ((price - s.entry) / s.entry * 100
                           * (1 if s.side == "LONG" else -1))
                    lines.append(f"📌 AÇIK [{tf}] {s.side} @{s.entry:.2f} → "
                                 f"{chg:+.2f}% (5x {chg * 5:+.2f}%)")
            else:
                lines.append("📌 Açık kâğıt işlem yok.")

        if self.last_learn:
            yas = (datetime.now(timezone.utc) - self.last_learn).total_seconds() / 3600
            lines.append(f"\n🏆 En iyi kurallar (öğrenme {yas:.1f} saat önce, win% sırası):")
            for tf in ("15m", "1h", "4h"):
                for ln in (self.best.get(tf) or [])[:2]:
                    lines.append(f"[{tf}] {ln}")
            if not any(self.best.values()):
                lines.append("(hiçbir kombo kârlı çıkmadı — bu da bilgidir)")
        elif self.learning:
            lines.append("\n⏳ İlk tam öğrenme sürüyor (5-15 dk) — kurallar birazdan.")
        if self.learn_err:
            lines.append(f"⚠️ son öğrenme hatası: {self.learn_err}")

        catches = self.recent_catches()
        if catches:
            lines.append("\n🎣 Son 48 saatte kuralların yakaladıkları (simülasyon):")
            lines += catches
        lines.append("\nNot: geçmiş performans gelecek garantisi değil; "
                     "n<30 kombolar fikirdir, kanıt değil.")
        return "\n".join(lines)

    # ---- ana döngü ----------------------------------------------------------
    def run(self) -> None:
        logger.info(f"Öğrenme daemon'u başladı: {self.symbol} | rapor {REPORT_SEC}s | "
                    f"tam öğrenme {LEARN_EVERY_H}h")
        if self.bot is None:
            self.send(f"🧠 Öğrenme daemon'u başladı ({self.symbol}). "
                      f"10 dk'da bir rapor, {LEARN_EVERY_H} saatte bir tam öğrenme. "
                      f"Komut dinlemez — komutlar bottadır.")
        while True:
            if not self.learning and (
                    self.last_learn is None or
                    datetime.now(timezone.utc) - self.last_learn
                    > timedelta(hours=LEARN_EVERY_H)):
                threading.Thread(target=self.full_learn, daemon=True).start()
            try:
                self.send(self.rapor())
            except Exception as e:  # noqa: BLE001
                logger.warning(f"rapor hatası: {e}")
            time.sleep(REPORT_SEC)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="Sürekli Öğrenme Daemon'u")
    ap.add_argument("--symbol", default="ETHUSDT")
    args = ap.parse_args()
    OgrenmeDaemon(symbol=args.symbol).run()


if __name__ == "__main__":
    main()
