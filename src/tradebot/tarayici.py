"""Coin Tarayıcı — ana botla AYNI kurallar, BTC + LINK, ayrı pencere.

Kadro 11.07.2026'da 12 coinden 2'ye indirildi (kullanıcı isteği: ETH+BTC+1).
LINK seçimi veriyle: 60g yeni kurallarla n=23 toplam +%17.4, iki yarı da artı
(SOL/BNB ikinci yarıda eksi, elendi). BTC 60g'de eksiydi (-%8.7) — kullanıcı
istedi, karne toplayıp kanıtla konuşacağız.

Ne yapar:
  * Coinleri (ETH hariç — o ana botta) her ~60 saniyede tarar.
  * Ana botun AYNI kural setini kullanır (Copilot.analyze: trend+pullback,
    gece/akşam filtreleri, dip-kovalama koruması, BE-1.5R...).
  * Kurulum çıkan coinde ANINDA Telegram uyarısı + kâğıt işlem açar.
  * Kapanışları 5m mum taramasıyla tespit eder (BE dahil, kötümser sayım),
    ✅/🛑/😐 raporlar; kendi karnesini ayrı tutar (tarayici_journal.db).
  * Saatte bir kısa özet: açık işlemler + karne.

ÖNEMLİ:
  * Telegram'a SADECE MESAJ GÖNDERİR — komut dinlemez (ana botla ÇAKIŞMAZ).
  * GERÇEK EMİR AÇMAZ — kâğıt işlem. Kurallar ETH'nin 90 gününde test edildi;
    diğer coinlerde kanıt TOPLAMA aşamasındayız — karne bunun için.
  * İkinci-giriş (reclaim) kuralı tarayıcıda YOK (sade tutuldu).

Kullanım: normalde telegram_bot içinde otomatik başlar (TEK UYGULAMA).
Tek başına: python -m tradebot.tarayici
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import requests
from loguru import logger

from tradebot.config import STATE_DIR, Secrets
from tradebot.copilot import PLANS, Copilot, Setup
from tradebot.datafeed import make_feed
from tradebot.journal import Journal

COINS = ("BTCUSDT", "LINKUSDT")
CHAT_FILE = STATE_DIR / "telegram_chat.json"
STATE_FILE = STATE_DIR / "tarayici_state.json"
DB_FILE = STATE_DIR / "tarayici_journal.db"
TARAMA_SN = 60          # tam tur hedef süresi
OZET_SN = 3600          # saatlik özet


def fmt(p: float) -> str:
    """Coin fiyatı biçimi: BTC 117234.50, DOGE 0.1834."""
    return f"{p:,.2f}" if p >= 10 else f"{p:.4f}"


class Tarayici:
    def __init__(self):
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
        if self.chat_id is None:
            print("HATA: telegram_chat.json yok — önce ana bota /start yazılmalı.")
            sys.exit(1)
        self.feed = make_feed()
        self.journal = Journal(DB_FILE)
        self.cps: dict[str, Copilot] = {}
        for s in COINS:
            cp = Copilot(symbol=s)
            cp.feed = self.feed          # tek API istemcisi paylaşılır
            cp.journal = self.journal    # tarayıcının kendi karnesi
            self.cps[s] = cp
        # key "SYM|tf" -> (Setup, aid, ts)
        self.active: dict[str, tuple[Setup, int, str]] = {}
        # SOĞUMA: stop yiyen coin/plan hemen yeniden giremez (10.07: LINK 15 dk
        # arayla iki kez aynı kırılıma girdi, ikisi de stop). key -> kadar (UTC)
        self.cooldown: dict[str, datetime] = {}
        # Tek-uygulama modu (telegram_bot içinde thread): banner ve saatlik özet
        # ana bottan yönetilir — kapatılır ki aynı saatte 3 ayrı mesaj düşmesin.
        self.banner = True
        self.ozet_acik = True
        self._load()

    # ---- telegram: SADECE gönderme ------------------------------------------
    def send(self, text: str) -> None:
        for i in range(0, len(text), 3900):
            try:
                requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                              json={"chat_id": self.chat_id, "text": text[i:i + 3900]},
                              timeout=35)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"telegram gönderilemedi: {e}")

    # ---- durum dosyası -------------------------------------------------------
    def _load(self) -> None:
        if not STATE_FILE.exists():
            return
        try:
            st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            self.active = {k: (Setup(**v["setup"]), v["aid"], v["ts"])
                           for k, v in st.get("active", {}).items()}
            now = datetime.now(timezone.utc)
            self.cooldown = {k: t for k, v in st.get("cooldown", {}).items()
                             if (t := datetime.fromisoformat(v)) > now}
        except Exception:  # noqa: BLE001
            pass

    def _save(self) -> None:
        STATE_FILE.write_text(json.dumps({
            "active": {k: {"setup": asdict(s), "aid": aid, "ts": ts}
                       for k, (s, aid, ts) in self.active.items()},
            "cooldown": {k: t.isoformat() for k, t in self.cooldown.items()},
        }, ensure_ascii=False), encoding="utf-8")

    # ---- kapanış taraması (ana botla aynı mantık: 5m replay + BE) ------------
    def _resolve_hit(self, sym: str, a: Setup, ats: str):
        since = datetime.fromisoformat(ats)
        mins = (datetime.now(timezone.utc) - since).total_seconds() / 60
        limit = min(1000, max(3, int(mins / 5) + 3))
        d = self.feed.klines(sym, "5m", limit)
        stop, armed = a.stop, False
        for _, row in d.iterrows():
            if row["open_time"] < since:
                continue
            if a.side == "LONG":
                if row["low"] <= stop:
                    return ("BE" if armed else "STOP"), stop, row["open_time"]
                if row["high"] >= a.target:
                    return "HEDEF", a.target, row["open_time"]
                if a.be_at and not armed and row["high"] >= a.be_at:
                    armed, stop = True, a.entry
            else:
                if row["high"] >= stop:
                    return ("BE" if armed else "STOP"), stop, row["open_time"]
                if row["low"] <= a.target:
                    return "HEDEF", a.target, row["open_time"]
                if a.be_at and not armed and row["low"] <= a.be_at:
                    armed, stop = True, a.entry
        return None, None, None

    # ---- tek coin, tek plan --------------------------------------------------
    def _coin_tick(self, sym: str) -> None:
        cp = self.cps[sym]
        price = self.feed.mark_price(sym)
        for tf in PLANS:
            key = f"{sym}|{tf}"
            st = self.active.get(key)
            if st is None:
                soguma = self.cooldown.get(key)
                if soguma and datetime.now(timezone.utc) < soguma:
                    continue   # taze stop yedi — aynı kurulumu kovalamasın
                setup, _ = cp.analyze(tf)
                if not setup:
                    continue
                if (setup.side == "LONG" and price <= setup.stop) or \
                   (setup.side == "SHORT" and price >= setup.stop):
                    continue   # bayat
                aid = self.journal.add(sym, setup.side, setup.entry, setup.stop,
                                       setup.target, setup.adx, setup.reason,
                                       rsi=setup.rsi, vol_ratio=setup.vol_ratio,
                                       room_atr=setup.room_atr,
                                       sep_pct=setup.sep_pct, hour=setup.hour)
                self.active[key] = (setup, aid,
                                    datetime.now(timezone.utc).isoformat())
                saat = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%H:%M")
                be = (f"BE: fiyat {fmt(setup.be_at)} olursa stopu girişe çek\n"
                      if setup.be_at else "")
                self.send(f"🛰️ KURULUM {sym} [{tf}] {setup.side}  ({saat} TR)\n"
                          f"Giriş: {fmt(setup.entry)}  Stop: {fmt(setup.stop)}  "
                          f"Hedef: {fmt(setup.target)}\n{be}{setup.reason}\n"
                          f"(Kâğıt işlem — emir istersen SEN aç, 5x)\n"
                          f"Grafik: https://www.tradingview.com/chart/?symbol="
                          f"BINANCE:{sym}.P&interval={'15' if tf == '15m' else '60'}")
                logger.info(f"kurulum: {key} {setup.side} @{setup.entry}")
            else:
                a, aid, ats = st
                try:
                    hit, exit_px, hit_t = self._resolve_hit(sym, a, ats)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"{key} kapanış taraması: {e}")
                    continue
                if hit:
                    chg = ((exit_px - a.entry) / a.entry * 100
                           * (1 if a.side == "LONG" else -1))
                    self.journal.close(aid, hit, round(chg, 3))
                    em = {"HEDEF": "✅", "STOP": "🛑", "BE": "😐"}.get(hit, "❔")
                    ad = "BAŞABAŞ" if hit == "BE" else hit
                    saat = (hit_t + timedelta(hours=3)).strftime("%H:%M")
                    s = self.journal.summary()
                    ek = ""
                    if hit == "STOP":
                        dk = 90 if tf == "15m" else 240
                        self.cooldown[key] = (datetime.now(timezone.utc)
                                              + timedelta(minutes=dk))
                        ek = f"\n({sym} [{tf}] {dk} dk soğumada — hemen yeniden girmez)"
                    self.send(f"{em} 🛰️ {sym} [{tf}] {ad}: {a.side} {fmt(a.entry)} -> "
                              f"{fmt(exit_px)} ({chg:+.2f}% | 5x {chg * 5:+.2f}%) "
                              f"{saat} TR\nTarayıcı karnesi: {s['kapanan']} kapanan, "
                              f"win %{s['win_rate']}, toplam %{s['toplam_pnl_pct']}{ek}")
                    self.active.pop(key, None)
                elif a.be_at and not a.be_armed and (
                        price >= a.be_at if a.side == "LONG" else price <= a.be_at):
                    a.be_armed = True
                    self.send(f"📐 🛰️ {sym} [{tf}] {fmt(a.be_at)} görüldü — "
                              f"STOP girişe ({fmt(a.entry)}) çekildi (kâğıtta).")

    # ---- saatlik özet --------------------------------------------------------
    def ozet(self) -> str:
        saat = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%H:%M")
        lines = [f"🛰️ Tarayıcı özeti {saat} TR — {len(COINS)} coin izleniyor"]
        if self.active:
            for key, (a, _aid, _ats) in self.active.items():
                sym, tf = key.split("|")
                try:
                    p = self.feed.mark_price(sym)
                    chg = (p - a.entry) / a.entry * 100 * (1 if a.side == "LONG" else -1)
                    lines.append(f"📌 {sym} [{tf}] {a.side} @{fmt(a.entry)} → "
                                 f"%{chg:+.2f} (5x %{chg * 5:+.2f})")
                except Exception:  # noqa: BLE001
                    lines.append(f"📌 {sym} [{tf}] {a.side} @{fmt(a.entry)}")
        else:
            lines.append("📌 Açık kâğıt işlem yok — kurulum bekleniyor.")
        s = self.journal.summary()
        if s["kapanan"]:
            lines.append(f"📒 Karne: {s['kapanan']} kapanan, win %{s['win_rate']}, "
                         f"toplam %{s['toplam_pnl_pct']} (5x %{s['toplam_pnl_pct'] * 5:+.1f})")
        lines.append("Not: kâğıt işlem — kurallar ETH'de test edildi, "
                     "bu coinlerde kanıt topluyoruz.")
        return "\n".join(lines)

    # ---- ana döngü -----------------------------------------------------------
    def run(self) -> None:
        logger.info(f"Tarayıcı başladı: {len(COINS)} coin | tur ~{TARAMA_SN}s")
        if self.banner:
            self.send(f"🛰️ Coin tarayıcı başladı: {', '.join(c[:-4] for c in COINS)}.\n"
                      f"Ana botun kurallarıyla ~{TARAMA_SN} sn'de bir tam tur; kurulum "
                      f"çıkan coin ANINDA buraya düşer, kâğıt işlemle takip edilir. "
                      f"Komut dinlemez — komutlar ana bottadır.")
        son_ozet = time.time()
        while True:
            t0 = time.time()
            for sym in COINS:
                try:
                    self._coin_tick(sym)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"{sym} tur hatası: {e}")
            self._save()
            if self.ozet_acik and time.time() - son_ozet >= OZET_SN:
                son_ozet = time.time()
                try:
                    self.send(self.ozet())
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"özet hatası: {e}")
            kalan = TARAMA_SN - (time.time() - t0)
            if kalan > 0:
                time.sleep(kalan)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    Tarayici().run()


if __name__ == "__main__":
    main()
