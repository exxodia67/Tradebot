"""Telegram Bot — co-pilot'u cebine taşır. PC yerine sunucuda da çalışır.

Ne yapar:
  * Copilot çift planı (15m/2R + 1h/3R) arka planda döner; KURULUM / STOP /
    HEDEF / İKİNCİ GİRİŞ uyarılarını Telegram'a anında yollar.
  * Komutlar:
      /durum   -> 5m·15m·1h·4h·1d canlı çok-TF analiz tablosu
      /analiz  -> iki planın o anki değerlendirmesi (neden bekliyor / kurulum)
      /journal -> kapanan işlemlerin karnesi (win, PnL)
      /ogren   -> öğrenme raporu özeti (journal kovaları)
      /yardim  -> komut listesi
  * Her sabah 07:00 UTC otomatik günlük özet (durum + journal).
  * GERÇEK EMİR AÇMAZ — yol gösterme modu. Emri sen koyarsın.

Kurulum:
  1) Telegram'da @BotFather -> /newbot -> token al
  2) .env dosyasına ekle:  TELEGRAM_BOT_TOKEN=123456:ABC...
  3) python -m tradebot.telegram_bot   (veya 9-Telegram-Bot.bat)
  4) Telegram'da botuna /start yaz — bot seni tanır (chat id kaydeder)

PC kapalıyken çalışması için bu klasörü bir sunucuya (VPS / PythonAnywhere /
Raspberry Pi) kopyala, aynı komutu orada çalıştır. Kod değişikliği gerekmez.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import requests
from loguru import logger

from tradebot.config import ROOT, Secrets
from tradebot.copilot import PLANS, Copilot, Setup
from tradebot.exchange.binance_futures import klines_to_df
from tradebot.indicators import adx, atr, rsi, sma
from tradebot.journal import Journal

API = "https://api.telegram.org/bot{token}/{method}"
CHAT_FILE = ROOT / "telegram_chat.json"
STATE_FILE = ROOT / "tg_state.json"    # --once modunda durum (GitHub Actions)
TFS = ("5m", "15m", "1h", "4h", "1d")   # /durum çok-TF analizi


class TelegramBot:
    def __init__(self, symbol: str = "ETHUSDT"):
        self.token = Secrets().telegram_bot_token
        if not self.token:
            print("HATA: .env içinde TELEGRAM_BOT_TOKEN yok.")
            print("  1) Telegram'da @BotFather -> /newbot -> token al")
            print("  2) .env dosyasına ekle: TELEGRAM_BOT_TOKEN=123456:ABC...")
            sys.exit(1)
        self.chat_id: int | None = None
        if CHAT_FILE.exists():
            try:
                self.chat_id = json.loads(CHAT_FILE.read_text())["chat_id"]
            except Exception:  # noqa: BLE001
                pass
        self.symbol = symbol
        self.copilot = Copilot(symbol=symbol)
        self.journal = self.copilot.journal
        self._offset = 0
        self._last_daily: str | None = None

    # ---- telegram API ----------------------------------------------------
    def _api(self, method: str, **params):
        try:
            r = requests.post(API.format(token=self.token, method=method),
                              json=params, timeout=35)
            return r.json()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"telegram hatası: {e}")
            return {}

    def send(self, text: str) -> None:
        if self.chat_id is None:
            return
        # Telegram 4096 sınırı — uzunsa böl
        for i in range(0, len(text), 3900):
            self._api("sendMessage", chat_id=self.chat_id, text=text[i:i + 3900])

    # ---- analizler ---------------------------------------------------------
    def durum_text(self) -> str:
        c = self.copilot.client
        price = float(c.futures_mark_price(symbol=self.symbol)["markPrice"])
        tk = c.futures_ticker(symbol=self.symbol)
        lines = [f"📊 {self.symbol}  {price:.2f}",
                 f"24s: %{float(tk['priceChangePercent']):+.2f}  "
                 f"zirve {float(tk['highPrice']):.0f} / dip {float(tk['lowPrice']):.0f}", ""]
        for tf in TFS:
            d = klines_to_df(c.futures_klines(symbol=self.symbol, interval=tf, limit=120))
            m7, m25, m99 = (sma(d["close"], q).iloc[-1] for q in (7, 25, 99))
            a = float(adx(d, 14).iloc[-1])
            r = float(rsi(d["close"], 14).iloc[-1])
            diz = "⬆️" if m7 > m25 > m99 else "⬇️" if m7 < m25 < m99 else "↔️"
            poz = ">" if price > m7 else "<"
            lines.append(f"{tf:>3} {diz} ADX{a:2.0f} RSI{r:2.0f}  fiyat{poz}MA7({m7:.0f})")
        return "\n".join(lines)

    def analiz_text(self) -> str:
        parts = ["🔎 Plan değerlendirmesi:"]
        for tf in PLANS:
            setup, status = self.copilot.analyze(tf)
            if setup:
                parts.append(f"\n[{tf}] 🔔 {setup.side}\n"
                             f"giriş {setup.entry:.2f} stop {setup.stop:.2f} "
                             f"hedef {setup.target:.2f}\n{setup.reason}")
            else:
                parts.append(f"[{tf}] {status}")
        return "\n".join(parts)

    def journal_text(self) -> str:
        s = self.journal.summary()
        return (f"📒 Journal: {s['kapanan']} kapanan işlem\n"
                f"win %{s['win_rate']}  ort %{s['ort_pnl_pct']}  "
                f"toplam %{s['toplam_pnl_pct']}")

    # ---- komut döngüsü -----------------------------------------------------
    def _handle(self, text: str) -> str | None:
        t = text.strip().lower()
        if t.startswith("/start"):
            return ("Merhaba! Co-pilot bağlandı. 🤝\n"
                    "Uyarılar otomatik gelir. Komutlar: /durum /analiz /journal /ogren /yardim\n"
                    "⚠️ Emir AÇMAM — kurulum söylerim, emri sen koyarsın.")
        if t.startswith("/durum"):
            return self.durum_text()
        if t.startswith("/analiz"):
            return self.analiz_text()
        if t.startswith("/journal"):
            return self.journal_text()
        if t.startswith("/ogren"):
            return self.journal.learn_report()
        if t.startswith("/yardim") or t.startswith("/help"):
            return ("/durum — 5m·15m·1h·4h·1d canlı tablo\n"
                    "/analiz — planlar şu an ne düşünüyor\n"
                    "/journal — işlem karnesi\n"
                    "/ogren — öğrenme raporu\n")
        return None

    def _process_updates(self, timeout: int) -> None:
        """Bekleyen Telegram mesajlarını işler (timeout=0 -> tek geçiş)."""
        res = self._api("getUpdates", offset=self._offset + 1, timeout=timeout)
        for u in res.get("result", []):
            self._offset = max(self._offset, u["update_id"])
            msg = u.get("message") or {}
            text = msg.get("text", "")
            cid = (msg.get("chat") or {}).get("id")
            if not text or cid is None:
                continue
            if self.chat_id is None:   # ilk yazan sahibi olur
                self.chat_id = cid
                CHAT_FILE.write_text(json.dumps({"chat_id": cid}))
                logger.info(f"chat kaydedildi: {cid}")
            if cid != self.chat_id:    # yabancı sohbetleri yok say
                continue
            reply = self._handle(text)
            if reply:
                self.send(reply)

    def poll_loop(self) -> None:
        logger.info("Telegram komut dinleyici başladı.")
        while True:
            try:
                self._process_updates(timeout=30)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"poll hatası: {e}")
                time.sleep(5)

    # ---- copilot tek geçiş (hem sürekli mod hem --once bunu kullanır) -------
    def _tick(self, active: dict) -> None:
        cp = self.copilot
        price = float(cp.client.futures_mark_price(symbol=self.symbol)["markPrice"])
        for tf in PLANS:
            st = active.get(tf)
            if st is None:
                setup = cp.analyze_reentry(tf, price)
                if setup is None:
                    setup, _ = cp.analyze(tf)
                if setup:
                    aid = self.journal.add(
                        self.symbol, setup.side, setup.entry, setup.stop,
                        setup.target, setup.adx, setup.reason,
                        rsi=setup.rsi, vol_ratio=setup.vol_ratio,
                        room_atr=setup.room_atr, sep_pct=setup.sep_pct,
                        hour=setup.hour)
                    active[tf] = (setup, aid)
                    self.send(f"🔔 KURULUM [{tf}] {setup.side}\n"
                              f"Giriş: {setup.entry:.2f}\nStop: {setup.stop:.2f}\n"
                              f"Hedef: {setup.target:.2f}\n{setup.reason}\n"
                              f"(Emri ve STOP'u SEN koy — 5x)")
                    cp.say(f"TG uyarı: [{tf}] {setup.side} {setup.entry:.2f}")
            else:
                a, aid = st
                chg = (price - a.entry) / a.entry * 100 * (1 if a.side == "LONG" else -1)
                hit = None
                if a.side == "LONG":
                    if price <= a.stop: hit = "STOP"
                    elif price >= a.target: hit = "HEDEF"
                else:
                    if price >= a.stop: hit = "STOP"
                    elif price <= a.target: hit = "HEDEF"
                if hit:
                    self.journal.close(aid, hit, round(chg, 3))
                    emoji = "✅" if hit == "HEDEF" else "🛑"
                    msg = (f"{emoji} [{tf}] {hit}: {a.side} {a.entry:.2f} -> {price:.2f} "
                           f"({chg:+.2f}% | 5x {chg * 5:+.2f}%)")
                    if hit == "STOP":
                        cp._reentry[tf] = {
                            "side": a.side, "entry": a.entry,
                            "until": datetime.now(timezone.utc) + timedelta(hours=4)}
                        msg += "\n(4 saat ikinci-giriş nöbeti başladı)"
                    self.send(msg)
                    cp.say(f"TG kapanış: {msg}")
                    active.pop(tf, None)

        # günlük özet 07:00 UTC
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        if now.hour == 7 and self._last_daily != today:
            self._last_daily = today
            self.send("🌅 Günlük özet\n\n" + self.durum_text() +
                      "\n\n" + self.journal_text())

    def copilot_loop(self, interval: int = 60) -> None:
        active: dict[str, tuple] = {}
        logger.info("Copilot döngüsü başladı (telegram modu).")
        while True:
            try:
                self._tick(active)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"copilot tick hatası: {e}")
            time.sleep(interval)

    # ---- GitHub Actions modu: tek geçiş, durum dosyada saklanır --------------
    def run_once(self) -> None:
        """Tek geçiş: komutları cevapla + planları kontrol et + durumu kaydet.

        GitHub Actions cron'u (ör. 15 dk'da bir) bunu çağırır; PC kapalıyken
        bedava 7/24 çalışma yolu. Bedel: 15 dk'ya kadar gecikme.
        """
        st: dict = {}
        if STATE_FILE.exists():
            try:
                st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass
        self._offset = st.get("offset", 0)
        self._last_daily = st.get("last_daily")
        # ikinci-giriş nöbetlerini geri yükle
        ren: dict = {}
        for tf, r in (st.get("reentry") or {}).items():
            until = datetime.fromisoformat(r["until"])
            if until > datetime.now(timezone.utc):
                ren[tf] = {"side": r["side"], "entry": r["entry"], "until": until}
        self.copilot._reentry = ren
        # açık kurulumları geri yükle
        active: dict = {}
        for tf, a in (st.get("active") or {}).items():
            active[tf] = (Setup(**a["setup"]), a["aid"])

        self._process_updates(timeout=0)   # bekleyen komutları cevapla
        try:
            self._tick(active)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"tick hatası: {e}")

        STATE_FILE.write_text(json.dumps({
            "offset": self._offset,
            "last_daily": self._last_daily,
            "reentry": {tf: {"side": r["side"], "entry": r["entry"],
                             "until": r["until"].isoformat()}
                        for tf, r in self.copilot._reentry.items()},
            "active": {tf: {"setup": asdict(s), "aid": aid}
                       for tf, (s, aid) in active.items()},
        }, ensure_ascii=False), encoding="utf-8")
        logger.info("tek geçiş bitti, durum kaydedildi.")

    def run(self) -> None:
        t = threading.Thread(target=self.copilot_loop, daemon=True)
        t.start()
        if self.chat_id:
            self.send("🤖 Co-pilot yeniden başladı. /durum ile kontrol et.")
        self.poll_loop()   # ana thread komutları dinler


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    import argparse
    ap = argparse.ArgumentParser(description="Telegram co-pilot botu")
    ap.add_argument("--symbol", default="ETHUSDT")
    ap.add_argument("--once", action="store_true",
                    help="tek geçiş (GitHub Actions cron modu)")
    args = ap.parse_args()
    bot = TelegramBot(symbol=args.symbol)
    if args.once:
        bot.run_once()
    else:
        bot.run()


if __name__ == "__main__":
    main()
