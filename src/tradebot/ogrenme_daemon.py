"""Sürekli Öğrenme + Saatlik Rapor modülü (botun içinde gömülü çalışır).

Ne yapar:
  * TAM ÖĞRENME (başlangıçta + 6 saatte bir, arka plan thread):
    90 günlük walk-forward, 4 zaman dilimi, 10 pattern. En yüksek win oranlı
    kombinasyonlar çıkarılır, ogrenme_ozet.md dosyasına yazılır.
  * HER SAAT: düz Türkçe rapor —
      - 15dk / 1saat / 4saat / 1gün trendleri (YÜKSELİŞ/DÜŞÜŞ/KARARSIZ)
      - açık kâğıt işlemler (anlık kâr/zarar, 5x)
      - şu an açılabilir işlem var mı, yoksa neden yok
      - SON 24 SAAT SİMÜLASYONU: botun kendi kurallarıyla açsaydık ne olurdu
        (aynı analyze() kodu — kural kopyası yok)
      - karne özeti (gerçek uyarılar)

ÖNEMLİ: Telegram'a SADECE MESAJ GÖNDERİR — komut DİNLEMEZ (getUpdates yok).
Kurulum uyarıları bu modülün işi değil; onlar bottan ANINDA gider.

Kullanım: normalde telegram_bot içinden otomatik başlar. Tek başına:
    python -m tradebot.ogrenme_daemon
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

from tradebot.config import ROOT, STATE_DIR, Secrets
from tradebot.copilot import PLANS, Copilot
from tradebot.exchange.binance_futures import fetch_mainnet_klines
from tradebot.indicators import adx, sma
from tradebot.learner import _stats, add_higher_trend, adx_b, prep, vol_b, walk_forward

CHAT_FILE = STATE_DIR / "telegram_chat.json"
OZET_MD = ROOT / "ogrenme_ozet.md"
LEARN_EVERY_H = 6      # tam öğrenme aralığı (saat)
REPORT_SEC = 3600      # rapor aralığı: SAATTE BİR

# (giriş TF, trend TF, gün, S/R penceresi, max_hold, taze-bar limiti)
LEARN_PLANS = (
    ("5m",  "15m", 45, 288, 96, 900),
    ("15m", "1h",  90, 96,  96, 900),
    ("1h",  "1d",  90, 24,  72, 900),
    ("4h",  "1d",  240, 42, 42, 600),
)

# learner pattern adları -> düz Türkçe (raporda jargon olmasın)
PAT_TR = {
    "P1_TREND": "trend takibi", "P2_PULLBACK": "MA25 geri çekilme",
    "P3_BREAKOUT": "hacimli kırılım", "P4_KESISIM": "MA kesişimi",
    "P5_RSI_DONUS": "RSI dönüşü", "P6_YUTAN": "yutan mum",
    "P7_PINBAR": "iğne mum", "P8_SIKISMA": "sıkışma kırılımı",
    "P9_SEKME_MR": "destek/direnç sekmesi", "P10_ICBAR": "iç bar kırılımı",
}


class OgrenmeDaemon:
    def __init__(self, symbol: str = "ETHUSDT", bot=None):
        self.symbol = symbol
        self.bot = bot          # gömülü mod: botun copilot'unu ve chat'ini kullanır
        if bot is not None:
            self.token = bot.token
            self.copilot = bot.copilot
        else:
            self.token = Secrets().telegram_bot_token
            if not self.token:
                print("HATA: .env içinde TELEGRAM_BOT_TOKEN yok.")
                sys.exit(1)
            self.copilot = Copilot(symbol=symbol)
        self.feed = self.copilot.feed
        self.chat_id = None
        if CHAT_FILE.exists():
            try:
                self.chat_id = json.loads(CHAT_FILE.read_text())["chat_id"]
            except Exception:  # noqa: BLE001
                pass
        if self.chat_id is None and bot is None:
            print("HATA: telegram_chat.json yok — önce bota /start yazılmış olmalı.")
            sys.exit(1)
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
                    lines.append(f"{PAT_TR.get(k[0], k[0])} {k[1]} — {kn} işlemde "
                                 f"win %{kw:.0f}, ort %{ka:+.2f}")
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

    # ---- son 24 saat: BOTUN KENDİ kurallarıyla açsaydık ne olurdu -----------
    def son24_simulasyon(self) -> list[str]:
        """Bar-kapanışlarında copilot.analyze() koşturur, 5m mumla sonuçlandırır.

        Bire bir aynı kural kodu; tek fark girişin mum kapanışında sayılması
        (canlıda bot anlık fiyatla girer — küçük sapma olabilir).
        """
        out: list[str] = []
        cp = self.copilot
        d5 = self.feed.klines(self.symbol, "5m", 400)
        toplam, kazanc, kayip = 0.0, 0, 0
        for tf, nbars in (("15m", 96), ("1h", 24)):
            p = PLANS[tf]
            d_lo = self.feed.klines(self.symbol, tf, 500)
            d_hi = self.feed.klines(self.symbol, p["trend"], 500)
            busy_end = None
            for i in range(max(120, len(d_lo) - nbars), len(d_lo)):
                t = d_lo["open_time"].iloc[i]
                if busy_end is not None and t <= busy_end:
                    continue
                lo = d_lo.iloc[: i + 1]
                hi = d_hi[d_hi["open_time"] <= t]
                px = float(lo["close"].iloc[-1])
                try:
                    s, _ = cp.analyze(tf, d_hi=hi, d_lo=lo, price=px, now=t)
                except Exception:  # noqa: BLE001
                    continue
                if not s:
                    continue
                hit, exit_px, hit_t = None, None, None
                stop, armed = s.stop, False   # BE kuralı botla birebir aynı
                for _, r in d5[d5["open_time"] > t].iterrows():
                    stop_hit = (r["low"] <= stop if s.side == "LONG"
                                else r["high"] >= stop)
                    tgt_hit = (r["high"] >= s.target if s.side == "LONG"
                               else r["low"] <= s.target)
                    if stop_hit:                       # aynı mumda ikisi de: kötümser
                        hit = "BE" if armed else "STOP"
                        exit_px, hit_t = stop, r["open_time"]
                        break
                    if tgt_hit:
                        hit, exit_px, hit_t = "HEDEF", s.target, r["open_time"]
                        break
                    if s.be_at and not armed and (
                            r["high"] >= s.be_at if s.side == "LONG"
                            else r["low"] <= s.be_at):
                        armed, stop = True, s.entry
                saat_tr = f"{(t + timedelta(hours=3)):%d.%m %H:%M} TR"
                yon = "alış (LONG)" if s.side == "LONG" else "satış (SHORT)"
                if hit:
                    chg = ((exit_px - s.entry) / s.entry * 100
                           * (1 if s.side == "LONG" else -1))
                    toplam += chg
                    kazanc += chg > 0
                    kayip += chg <= 0
                    em = {"HEDEF": "✅", "STOP": "🛑", "BE": "😐"}.get(hit, "❔")
                    ad = "BAŞABAŞ" if hit == "BE" else hit
                    out.append(f"{em} {saat_tr} [{tf}] {yon} {s.entry:.0f}$ → "
                               f"{ad} %{chg:+.2f} (5x %{chg * 5:+.1f})")
                    busy_end = hit_t
                else:
                    out.append(f"⏳ {saat_tr} [{tf}] {yon} {s.entry:.0f}$ → "
                               f"hâlâ açık olurdu")
                    busy_end = d_lo["open_time"].iloc[-1]
        if out and (kazanc + kayip):
            out.append(f"Net: {kazanc + kayip} işlem ({kazanc} kazanç, {kayip} kayıp) "
                       f"→ toplam %{toplam:+.2f} (5x %{toplam * 5:+.1f})")
        return out

    # ---- trendleri düz Türkçe söyle -----------------------------------------
    def _trend_soz(self, tf: str) -> str:
        d = self.feed.klines(self.symbol, tf, 120)
        m7, m25, m99 = (float(sma(d["close"], q).iloc[-1]) for q in (7, 25, 99))
        a = float(adx(d, 14).iloc[-1])
        if m7 > m25 > m99:
            yon = "YÜKSELİŞ ⬆️"
        elif m7 < m25 < m99:
            yon = "DÜŞÜŞ ⬇️"
        else:
            return "KARARSIZ ↔️ — net yön yok"
        guc = "güçlü" if a >= 30 else "orta" if a >= 20 else "zayıf"
        return f"{yon} ({guc})"

    # ---- saatlik rapor -------------------------------------------------------
    def rapor(self) -> str:
        saat = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%H:%M")
        lines = [f"📣 SAATLİK RAPOR — {saat} TR"]
        price = None
        try:
            price = self.feed.mark_price(self.symbol)
            tk = self.feed.ticker24(self.symbol)
            lines.append(f"{self.symbol}: {price:.0f}$ "
                         f"(24 saatte %{float(tk['priceChangePercent']):+.1f})")
        except Exception as e:  # noqa: BLE001
            lines.append(f"fiyat okunamadı: {e}")

        lines.append("\n📈 TRENDLER")
        for tf, ad in (("15m", "15 dakika"), ("1h", "1 saat"),
                       ("4h", "4 saat"), ("1d", "1 gün")):
            try:
                lines.append(f"{ad}: {self._trend_soz(tf)}")
            except Exception as e:  # noqa: BLE001
                lines.append(f"{ad}: okunamadı ({e})")
        try:
            from tradebot.tradingview import tv_ozet_satiri
            tv = tv_ozet_satiri(self.symbol)
            if tv:
                lines.append(tv + "  (detay: /tv)")
        except Exception:  # noqa: BLE001
            pass

        lines.append("\n💼 AÇIK KÂĞIT İŞLEM")
        aktif = getattr(self.bot, "_active", {}) if self.bot is not None else {}
        if aktif and price is not None:
            for tf, (s, _aid, _ats) in aktif.items():
                chg = ((price - s.entry) / s.entry * 100
                       * (1 if s.side == "LONG" else -1))
                lines.append(f"[{tf}] {s.side} @{s.entry:.2f} → şu an %{chg:+.2f} "
                             f"(5x %{chg * 5:+.2f}) | stop {s.stop:.2f} hedef {s.target:.2f}")
        else:
            lines.append("Yok — bot kurulum bekliyor.")

        lines.append("\n🎯 ŞU AN AÇILABİLİR İŞLEM")
        for tf in PLANS:
            try:
                setup, status = self.copilot.analyze(tf)
                if setup:
                    lines.append(f"[{tf}] VAR — {setup.side}. Uyarısı ayrıca gidiyor.")
                else:
                    # status = "teknik okuma · sebep" — rapora sade SEBEP yeter
                    sebep = status.rsplit("·", 1)[-1].strip()
                    lines.append(f"[{tf}] yok — {sebep}")
            except Exception as e:  # noqa: BLE001
                lines.append(f"[{tf}] analiz hatası: {e}")

        lines.append("\n⏪ SON 24 SAAT — bot kurallarıyla açsaydık (SİMÜLASYON, "
                     "gerçek işlem değil)")
        try:
            sim = self.son24_simulasyon()
            lines += sim or ["Kurallar son 24 saatte hiç giriş üretmedi "
                             "(filtreler eledi — işlem yapmamak da karardır)."]
        except Exception as e:  # noqa: BLE001
            lines.append(f"simülasyon hatası: {e}")

        try:
            s = self.copilot.journal.summary()
            if s["kapanan"]:
                lines.append(f"\n📒 KARNE (gerçek uyarılar): {s['kapanan']} kapanan, "
                             f"win %{s['win_rate']}, toplam %{s['toplam_pnl_pct']} "
                             f"(5x %{s['toplam_pnl_pct'] * 5:+.1f}) — detay: /journal")
        except Exception:  # noqa: BLE001
            pass

        if self.learning and not self.last_learn:
            lines.append("\n⏳ İlk tam öğrenme sürüyor (5-15 dk).")
        elif self.last_learn:
            enler = [f"[{tf}] {(self.best.get(tf) or ['—'])[0]}"
                     for tf in ("15m", "1h") if self.best.get(tf)]
            if enler:
                lines.append("\n🏆 90 günde en iyi çalışan kural:")
                lines += enler
        if self.learn_err:
            lines.append(f"⚠️ son öğrenme hatası: {self.learn_err}")

        lines.append("\n⚠️ Garanti işlem YOK ve olamaz — hedef: stopu küçük tutup "
                     "toplamda artıda kalmak. Geçmiş performans söz vermez.")
        return "\n".join(lines)

    # ---- ana döngü ----------------------------------------------------------
    def run(self) -> None:
        logger.info(f"Öğrenme modülü başladı: {self.symbol} | rapor {REPORT_SEC}s | "
                    f"tam öğrenme {LEARN_EVERY_H}h")
        if self.bot is None:
            self.send(f"🧠 Öğrenme modülü başladı ({self.symbol}). Saatte bir rapor, "
                      f"{LEARN_EVERY_H} saatte bir tam öğrenme.")
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
    ap = argparse.ArgumentParser(description="Sürekli Öğrenme + Saatlik Rapor")
    ap.add_argument("--symbol", default="ETHUSDT")
    args = ap.parse_args()
    OgrenmeDaemon(symbol=args.symbol).run()


if __name__ == "__main__":
    main()
