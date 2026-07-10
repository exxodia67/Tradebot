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
import shutil
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import requests
from loguru import logger

from tradebot.config import ROOT, STATE_DIR, Secrets
from tradebot.copilot import PLANS, Copilot, Setup
from tradebot.indicators import adx, atr, rsi, sma
from tradebot.journal import Journal

API = "https://api.telegram.org/bot{token}/{method}"
# Durum dosyaları sabit ev-klasöründe — kurulum klasörü değişse de kaybolmaz.
CHAT_FILE = STATE_DIR / "telegram_chat.json"
STATE_FILE = STATE_DIR / "tg_state.json"
for _new, _old in ((CHAT_FILE, ROOT / "telegram_chat.json"),
                   (STATE_FILE, ROOT / "tg_state.json")):
    if not _new.exists() and _old.exists():
        try:
            shutil.copy2(_old, _new)   # eski kurulumdan tek seferlik taşıma
        except Exception:  # noqa: BLE001
            pass
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
        self._active: dict = {}   # tf -> (Setup, aid, ts) — /durum'da göstermek için
        # SOĞUMA: stop yiyen plan hemen yeniden analiz açamaz (tarayıcıdaki
        # 10.07 LINK çifte-stopunun ana bottaki karşılığı). tf -> kadar (UTC)
        self._cooldown: dict[str, datetime] = {}

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
        text = getattr(self, "tag", "") + text   # gece nöbetçisi 🌙 öneki
        # Telegram 4096 sınırı — uzunsa böl
        for i in range(0, len(text), 3900):
            self._api("sendMessage", chat_id=self.chat_id, text=text[i:i + 3900])

    # ---- analizler ---------------------------------------------------------
    def durum_text(self) -> str:
        feed = self.copilot.feed
        price = feed.mark_price(self.symbol)
        tk = feed.ticker24(self.symbol)
        lines = [f"📊 {self.symbol}  {price:.2f}",
                 f"24s: %{float(tk['priceChangePercent']):+.2f}  "
                 f"zirve {float(tk['highPrice']):.0f} / dip {float(tk['lowPrice']):.0f}", ""]
        for tf in TFS:
            d = feed.klines(self.symbol, tf, 120)
            m7, m25, m99 = (sma(d["close"], q).iloc[-1] for q in (7, 25, 99))
            a = float(adx(d, 14).iloc[-1])
            r = float(rsi(d["close"], 14).iloc[-1])
            diz = "⬆️" if m7 > m25 > m99 else "⬇️" if m7 < m25 < m99 else "↔️"
            poz = ">" if price > m7 else "<"
            lines.append(f"{tf:>3} {diz} ADX{a:2.0f} RSI{r:2.0f}  fiyat{poz}MA7({m7:.0f})")
        lines.append("")
        if self._active:
            for tf, (s, _aid, _ats) in self._active.items():
                chg = ((price - s.entry) / s.entry * 100
                       * (1 if s.side == "LONG" else -1))
                lines.append(f"📌 AÇIK kâğıt işlem [{tf}] {s.side} @{s.entry:.2f}  "
                             f"şu an {chg:+.2f}% (5x {chg * 5:+.2f}%)\n"
                             f"   stop {s.stop:.2f}  hedef {s.target:.2f}")
        else:
            lines.append("📌 Açık kâğıt işlem yok — kurulum bekleniyor.")
        return "\n".join(lines)

    def _bias(self, trend_tf: str) -> str | None:
        d = self.copilot.feed.klines(self.symbol, trend_tf, 120)
        h7, h25, h99 = (sma(d["close"], q).iloc[-1] for q in (7, 25, 99))
        return "LONG" if h7 > h25 > h99 else "SHORT" if h7 < h25 < h99 else None

    def analiz_text(self) -> str:
        feed = self.copilot.feed
        price = feed.mark_price(self.symbol)
        saat = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%H:%M")
        parts = [f"🔎 {self.symbol} {price:.2f} — plan değerlendirmesi ({saat} TR)"]

        # 4h çerçeve — yön haritası (240g testte 4h GİRİŞTE edge yoktu; pusula olarak var)
        try:
            d4 = feed.klines(self.symbol, "4h", 120)
            f7, f25, f99 = (sma(d4["close"], q).iloc[-1] for q in (7, 25, 99))
            a4 = float(adx(d4, 14).iloc[-1])
            r4 = float(rsi(d4["close"], 14).iloc[-1])
            res4 = float(d4["high"].iloc[-42:-2].max())   # son ~7 gün
            sup4 = float(d4["low"].iloc[-42:-2].min())
            diz4 = ("yukarı ⬆️" if f7 > f25 > f99
                    else "aşağı ⬇️" if f7 < f25 < f99 else "karışık ↔️")
            parts.append(f"\n🧭 4h çerçeve: {diz4} · ADX{a4:.0f} RSI{r4:.0f}\n"
                         f"   direnç {res4:.0f} / destek {sup4:.0f} · "
                         f"MA25 {f25:.0f} (fiyat {'üstünde ✓' if price > f25 else 'altında ✗'})")
            dir4 = "LONG" if f7 > f25 > f99 else "SHORT" if f7 < f25 < f99 else None
            b1d = self._bias("1d")
            if dir4 and b1d != dir4:
                parts.append(f"   ⚠️ 4h {('yukarı' if dir4 == 'LONG' else 'aşağı')} ama 1d "
                             f"{'belirsiz' if b1d is None else 'ters'} — dönüş dönemi "
                             f"olabilir; 1h planı 1d teyidini bekler (aceleci girmez)")
        except Exception as e:  # noqa: BLE001
            parts.append(f"\n🧭 4h çerçeve okunamadı: {e}")

        for tf in PLANS:
            p = PLANS[tf]
            baslik = f"\n[{tf}] plan — üst filtre {p['trend']}, hedef {p['rr']:.0f}R"
            try:
                setup, status = self.copilot.analyze(tf)
            except Exception as e:  # noqa: BLE001
                parts.append(f"{baslik}\n   analiz hatası: {e}")
                continue
            if setup:
                rp = abs(setup.entry - setup.stop) / setup.entry * 100
                tp = abs(setup.target - setup.entry) / setup.entry * 100
                parts.append(
                    f"{baslik}\n🔔 KURULUM VAR: {setup.side}\n"
                    f"   giriş {setup.entry:.2f} · stop {setup.stop:.2f} (-%{rp:.2f}) "
                    f"· hedef {setup.target:.2f} (+%{tp:.2f})\n"
                    f"   5x ile: stop -%{rp * 5:.1f} / hedef +%{tp * 5:.1f}\n"
                    f"   {setup.reason}")
                continue
            # kurulum yok: neden bekliyor + KURULSA pozisyon taslağı
            satir = [f"{baslik}", f"⏳ {status}"]
            try:
                bias = self._bias(p["trend"])
                d = feed.klines(self.symbol, tf, 120)
                e7, e25, e99 = (sma(d["close"], q).iloc[-1] for q in (7, 25, 99))
                ent_dir = ("LONG" if e7 > e25 > e99
                           else "SHORT" if e7 < e25 < e99 else None)
                if bias and ent_dir and ent_dir != bias:
                    # üst filtre ile giriş TF ters — plan bu çelişkide işlem üretmez;
                    # ters yönde taslak göstermek yanıltıcı olur
                    satir.append(
                        f"   ⚠️ Çelişki: {p['trend']} {bias} diyor ama {tf} grafiği "
                        f"{ent_dir} yönlü. Plan çelişkide işlem ÜRETMEZ.\n"
                        f"   {p['trend']} dönerse {ent_dir} taslağı gelir — dönüş "
                        f"sinyali olabilir, teyit bekleniyor.")
                elif bias:
                    atr_v = float(atr(d, 14).iloc[-1])
                    sd = atr_v * self.copilot.atr_stop_mult
                    stop = price - sd if bias == "LONG" else price + sd
                    tgt = (price + sd * p["rr"] if bias == "LONG"
                           else price - sd * p["rr"])
                    rp = sd / price * 100
                    tp = rp * p["rr"]
                    satir.append(
                        f"   Kurulursa taslak: {bias} giriş ~{price:.2f} · "
                        f"stop {stop:.2f} (-%{rp:.2f}) · hedef {tgt:.2f} (+%{tp:.2f})\n"
                        f"   5x ile: stop -%{rp * 5:.1f} / hedef +%{tp * 5:.1f} · R/R 1:{p['rr']:.0f}")
                else:
                    satir.append(f"   Taslak yok: {p['trend']} yönü belirsiz, plan yön seçemiyor.")
            except Exception:  # noqa: BLE001
                pass
            parts.append("\n".join(satir))
        return "\n".join(parts)

    def journal_text(self) -> str:
        s = self.journal.summary()
        out = [f"📒 Journal — botun GERÇEK uyarıları (simülasyonlar buraya girmez)",
               f"{s['kapanan']} kapanan işlem",
               f"win %{s['win_rate']}  ort %{s['ort_pnl_pct']}  "
               f"toplam %{s['toplam_pnl_pct']} (5x %{s['toplam_pnl_pct'] * 5:+.2f})",
               ""]
        for r in self.journal.last_trades(8):
            try:
                t = (datetime.fromisoformat(r["ts"]) + timedelta(hours=3)
                     ).strftime("%d.%m %H:%M")
            except Exception:  # noqa: BLE001
                t = r["ts"][:16]
            if r["outcome"]:
                em = {"HEDEF": "✅", "STOP": "🛑", "BE": "😐"}.get(r["outcome"], "🛑")
                out.append(f"{em} {t} {r['side']} {r['entry']:.2f} -> "
                           f"{r['outcome']} {r['pnl_pct']:+.2f}% "
                           f"(5x {r['pnl_pct'] * 5:+.1f}%)")
            else:
                out.append(f"⏳ {t} {r['side']} {r['entry']:.2f} AÇIK "
                           f"(stop {r['stop']:.2f} hedef {r['target']:.2f})")
        return "\n".join(out)

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
        if t.startswith("/tv"):
            from tradebot.tradingview import tv_text
            return tv_text(self.symbol)
        if t.startswith("/yardim") or t.startswith("/help"):
            return ("/durum — 5m·15m·1h·4h·1d canlı tablo\n"
                    "/analiz — planlar şu an ne düşünüyor\n"
                    "/tv — TradingView canlı AL/SAT analizi\n"
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

    def rapor_text(self) -> str:
        """10 dk'lık kısa rapor: fiyat, açık kâğıt işlemler, plan durumları."""
        price = self.copilot.feed.mark_price(self.symbol)
        saat = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%H:%M")
        lines = [f"🕙 {saat} TR — {self.symbol} {price:.2f}"]
        if self._active:
            for tf, (s, _aid, _ats) in self._active.items():
                chg = ((price - s.entry) / s.entry * 100
                       * (1 if s.side == "LONG" else -1))
                lines.append(f"📌 [{tf}] {s.side} @{s.entry:.2f} → {chg:+.2f}% "
                             f"(5x {chg * 5:+.2f}%)  stop {s.stop:.2f} hedef {s.target:.2f}")
        else:
            lines.append("📌 Açık kâğıt işlem yok.")
        for tf in PLANS:
            try:
                setup, status = self.copilot.analyze(tf)
                lines.append(f"[{tf}] " + ("🔔 kurulum VAR — uyarı ayrıca geldi"
                                           if setup else status))
            except Exception as e:  # noqa: BLE001
                lines.append(f"[{tf}] analiz hatası: {e}")
        return "\n".join(lines)

    # ---- durum dosyası (hem --once hem sürekli mod kullanır) ----------------
    def _load_state(self) -> dict:
        """STATE_FILE'dan offset/özet/nöbet/açık işlemleri yükler; active döner."""
        st: dict = {}
        if STATE_FILE.exists():
            try:
                st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass
        self._offset = st.get("offset", 0)
        self._last_daily = st.get("last_daily")
        ren: dict = {}
        for tf, r in (st.get("reentry") or {}).items():
            until = datetime.fromisoformat(r["until"])
            if until > datetime.now(timezone.utc):
                ren[tf] = {"side": r["side"], "entry": r["entry"], "until": until}
        self.copilot._reentry = ren
        now = datetime.now(timezone.utc)
        self._cooldown = {tf: t for tf, v in (st.get("cooldown") or {}).items()
                          if (t := datetime.fromisoformat(v)) > now}
        active: dict = {}
        for tf, a in (st.get("active") or {}).items():
            ats = a.get("ts")
            if not ats:   # eski durum dosyası: uyarı zamanını journal'dan al
                import sqlite3
                con = sqlite3.connect(self.journal.path)
                row = con.execute("SELECT ts FROM alerts WHERE id=?",
                                  (a["aid"],)).fetchone()
                con.close()
                ats = row[0] if row else datetime.now(timezone.utc).isoformat()
            active[tf] = (Setup(**a["setup"]), a["aid"], ats)
        self._active = active   # /durum açık işlemleri görsün
        return active

    def _save_state(self, active: dict) -> None:
        STATE_FILE.write_text(json.dumps({
            "offset": self._offset,
            "last_daily": self._last_daily,
            "reentry": {tf: {"side": r["side"], "entry": r["entry"],
                             "until": r["until"].isoformat()}
                        for tf, r in self.copilot._reentry.items()},
            "active": {tf: {"setup": asdict(s), "aid": aid, "ts": ats}
                       for tf, (s, aid, ats) in active.items()},
            "cooldown": {tf: t.isoformat() for tf, t in self._cooldown.items()},
        }, ensure_ascii=False), encoding="utf-8")

    # ---- copilot tek geçiş (hem sürekli mod hem --once bunu kullanır) -------
    def _resolve_hit(self, a: Setup, ats: str):
        """Uyarı anından bu yana 5m mumlarını tarar: stop/hedef değdi mi?

        Actions cron saatlerce atlayabilir; o anki fiyata bakmak aradaki
        STOP/HEDEF'i kaçırır. Mum taraması kaçırmaz. Aynı mumda ikisi de
        değmişse kötümser varsayım: STOP (learner ile aynı kural).
        Dönen: (hit, exit_px, hit_time) veya (None, None, None).
        """
        since = datetime.fromisoformat(ats)
        mins = (datetime.now(timezone.utc) - since).total_seconds() / 60
        limit = min(1000, max(3, int(mins / 5) + 3))
        d = self.copilot.feed.klines(self.symbol, "5m", limit)
        stop, armed = a.stop, False   # BE: +1.5R görülünce stop girişe (kötümser:
        for _, row in d.iterrows():   # aynı mumda önce stop bakılır, BE sonraki mumda)
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

    def _tick(self, active: dict) -> None:
        cp = self.copilot
        price = cp.feed.mark_price(self.symbol)
        for tf in PLANS:
            st = active.get(tf)
            if st is None:
                setup = cp.analyze_reentry(tf, price)
                if setup is None:
                    cd = self._cooldown.get(tf)
                    if cd and datetime.now(timezone.utc) < cd:
                        continue   # taze stop — soğuma bitene dek yeni analiz yok
                    setup, _ = cp.analyze(tf)
                if setup:
                    # tazelik: uyarı sana ulaştığında fiyat kaçmış olabilir
                    # (Actions cron gecikmeli). prog = giriş->hedef yolunun oranı.
                    prog = (price - setup.entry) / (setup.target - setup.entry)
                    if (setup.side == "LONG" and price <= setup.stop) or \
                       (setup.side == "SHORT" and price >= setup.stop):
                        cp.say(f"TG: [{tf}] kurulum bayat (fiyat stop tarafında), uyarı yok")
                        continue
                    aid = self.journal.add(
                        self.symbol, setup.side, setup.entry, setup.stop,
                        setup.target, setup.adx, setup.reason,
                        rsi=setup.rsi, vol_ratio=setup.vol_ratio,
                        room_atr=setup.room_atr, sep_pct=setup.sep_pct,
                        hour=setup.hour)
                    active[tf] = (setup, aid,
                                  datetime.now(timezone.utc).isoformat())
                    saat = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%H:%M")
                    if prog >= 0.35:
                        head = (f"⏰ GEÇ KALINDI [{tf}] {setup.side} — fiyat yolun "
                                f"%{prog * 100:.0f}'ini gitmiş. GİRME, R/R bozuldu.\n"
                                f"Kâğıt üstünde takip edeceğim (analiz için).")
                    else:
                        head = (f"🔔 KURULUM [{tf}] {setup.side}  ({saat} TR)\n"
                                f"Tazelik: yolun %{prog * 100:.0f}'i gitti "
                                f"(kural: %35 üstüyse girilmez)")
                    be_satir = (f"BE kuralı: fiyat {setup.be_at:.2f} olursa STOP'u "
                                f"girişe çek (haber vereceğim)\n" if setup.be_at else "")
                    tv_link = (f"https://www.tradingview.com/chart/?symbol="
                               f"BINANCE:{self.symbol}.P&interval="
                               f"{'15' if tf == '15m' else '60'}")
                    tv_gorus = ""
                    try:   # TV o an aynı yönde mi? (uyum ✓ / ÇELİŞKİ ⚠️)
                        from tradebot.tradingview import uyum_satiri
                        u = uyum_satiri(self.symbol, tf, setup.side)
                        if u:
                            tv_gorus = u + "\n"
                    except Exception:  # noqa: BLE001
                        pass
                    self.send(f"{head}\n"
                              f"Giriş: {setup.entry:.2f}  Şu an: {price:.2f}\n"
                              f"Stop: {setup.stop:.2f}\nHedef: {setup.target:.2f}\n"
                              f"{be_satir}{tv_gorus}{setup.reason}\n"
                              f"(Emri ve STOP'u SEN koy — 5x)\n"
                              f"Grafik: {tv_link}")
                    cp.say(f"TG uyarı: [{tf}] {setup.side} {setup.entry:.2f} (yol %{prog*100:.0f})")
            else:
                a, aid, ats = st
                try:
                    hit, exit_px, hit_time = self._resolve_hit(a, ats)
                except Exception as e:  # noqa: BLE001 — veri hatası: sonraki tikte dene
                    logger.warning(f"kapanış taraması hatası: {e}")
                    continue
                if hit:
                    chg = ((exit_px - a.entry) / a.entry * 100
                           * (1 if a.side == "LONG" else -1))
                    self.journal.close(aid, hit, round(chg, 3))
                    emoji = {"HEDEF": "✅", "STOP": "🛑", "BE": "😐"}.get(hit, "❔")
                    ad = "BAŞABAŞ (stop girişe çekilmişti)" if hit == "BE" else hit
                    saat = (hit_time + timedelta(hours=3)).strftime("%H:%M")
                    msg = (f"{emoji} [{tf}] {ad}: {a.side} {a.entry:.2f} -> {exit_px:.2f} "
                           f"({chg:+.2f}% | 5x {chg * 5:+.2f}%)\n"
                           f"Değme saati: {saat} TR (mum taramasıyla tespit)")
                    if hit == "STOP":
                        cp._reentry[tf] = {
                            "side": a.side, "entry": a.entry,
                            "until": datetime.now(timezone.utc) + timedelta(hours=4)}
                        dk = 90 if tf == "15m" else 240
                        self._cooldown[tf] = (datetime.now(timezone.utc)
                                              + timedelta(minutes=dk))
                        msg += (f"\n(4 saat ikinci-giriş nöbeti başladı; normal "
                                f"analiz {dk} dk soğumada)")
                    self.send(msg)
                    cp.say(f"TG kapanış: {msg}")
                    active.pop(tf, None)
                elif a.be_at and not a.be_armed:
                    # açık işlem +1.5R'ye ulaştıysa: stopu girişe çek uyarısı (bir kez)
                    if (a.side == "LONG" and price >= a.be_at) or \
                       (a.side == "SHORT" and price <= a.be_at):
                        a.be_armed = True
                        self.send(f"📐 [{tf}] {a.be_at:.2f} görüldü — STOP'u girişe "
                                  f"({a.entry:.2f}) çek. Bundan sonrası en kötü başabaş.")

        # günlük özet 07:00 UTC
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        if now.hour == 7 and self._last_daily != today:
            self._last_daily = today
            self.send("🌅 Günlük özet\n\n" + self.durum_text() +
                      "\n\n" + self.journal_text())

    def copilot_loop(self, interval: int = 60, report_sec: int = 3600) -> None:
        active = self._load_state()   # PC yeniden başlasa da açık işlemler kaybolmaz
        logger.info(f"Copilot döngüsü başladı ({len(active)} açık işlem yüklendi).")
        last_report = 0.0
        while True:
            try:
                self._tick(active)
                # öğrenme modülü çalışıyorsa saatlik raporu O atar (tek mesaj olsun)
                if (not getattr(self, "_has_learn", False)
                        and time.time() - last_report >= report_sec):
                    last_report = time.time()
                    self.send(self.rapor_text())
                self._save_state(active)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"copilot tick hatası: {e}")
            time.sleep(interval)

    # ---- GitHub Actions modu: GECE NÖBETÇİSİ ---------------------------------
    def _pc_alive(self) -> bool:
        """PC'deki bot komut dinliyor mu? Telegram ikinci dinleyiciye 409 döner.

        PC'deki bot getUpdates'i uzun-poll ile sürekli tutar; biz de timeout=0
        ile dokunuruz. 409 Conflict = PC canlı. offset göndermediğimiz için
        hiçbir mesajı TÜKETMEZ — PC'nin kuyruğu bozulmaz.
        """
        try:
            r = requests.post(API.format(token=self.token, method="getUpdates"),
                              json={"timeout": 0, "limit": 1}, timeout=15)
            return r.status_code == 409
        except Exception:  # noqa: BLE001
            return False   # emin olamadıysak nöbeti al — uyarı kaçırmak daha kötü

    def run_once(self) -> None:
        """Bulut nöbetçisi: PC KAPALIYSA tek geçiş yapar (GitHub Actions cron'u).

        Önce Telegram'a dokunur: PC'deki bot dinliyorsa (409) nöbet ondadır,
        hiçbir şey yapmadan çıkar. PC kapalıysa planları kontrol eder,
        uyarıları 🌙 önekiyle yollar. Komut CEVAPLAMAZ (PC ile çift cevap
        riski) — komutlar PC açılınca ana botta.
        """
        if self._pc_alive():
            logger.info("PC'deki bot canlı (Telegram 409) — nöbet onda, çıkıyorum.")
            return
        self.tag = "🌙 "
        active = self._load_state()
        try:
            self._tick(active)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"tick hatası: {e}")
        self._save_state(active)
        logger.info("gece nöbeti geçişi bitti, durum kaydedildi.")

    def run(self) -> None:
        t = threading.Thread(target=self.copilot_loop, daemon=True)
        t.start()
        # öğrenme modülü aynı pencerede — ayrı bat/pencere GEREKMEZ
        self._has_learn = False
        try:
            from tradebot.ogrenme_daemon import OgrenmeDaemon
            threading.Thread(target=OgrenmeDaemon(self.symbol, bot=self).run,
                             daemon=True).start()
            self._has_learn = True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"öğrenme modülü başlatılamadı: {e}")
        if self.chat_id:
            self.send("🤖 Co-pilot başladı: kurulum çıkınca ANINDA uyarı + kâğıt işlem "
                      "takibi + SAATTE BİR sade rapor — hepsi bu pencerede. "
                      "/durum ile kontrol et.")
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
