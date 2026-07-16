"""Canlı Uyarı Co-pilot — piyasayı sürekli izler, KALİTELİ kurulum çıkınca uyarır.

Yol gösterme modu: gerçek emir AÇMAZ. Sadece analiz eder, sana net bir plan
(giriş/stop/hedef/kaldıraç) söyler ve her uyarıyı journal'a kaydeder. Sen uygularsın.

ÇİFT PLAN (v4 — learner walk-forward bulgularıyla):
  [15m] giriş: 1h trend filtresi, hedef 2R   (sık ama küçük fırsatlar)
  [1h]  giriş: 1d trend filtresi, hedef 3R   (seyrek ama en verimli — 180g testte
        iyi patternler ort +%0.50/işlem; 4h filtre de denendi, 1d KAZANDI)

Kural seti:
  1) Üst TF TREND yönü      -> yön filtresi (yukarı/aşağı/belirsiz)
  2) Giriş TF MA7/25/99     -> üst TF ile aynı yönde dizili olmalı
  3) WHIPSAW filtresi       -> MA'lar yapışıksa (düz piyasa) İŞLEM YOK
  4) ADX >= eşik            -> gerçek trend var [learner: DOĞRULANDI; 1h planında
                               eşik 25 — ADX30+ girişler açık ara en iyi]
  5) HACİM teyidi           -> VARSAYILAN KAPALI (learner: 15m'de ters çalıştı).
                               Kayıt sürüyor; --vol-mult ile açılabilir.
  6) RSI aşırılık koruması  -> gevşek eşikler (78/22): sadece uç blow-off'u engeller
  7) DİRENÇ/DESTEK yeri     -> hedef 24s zirve/dibe SIĞMALI [learner: DOĞRULANDI].
                               İSTİSNA "açık gökyüzü": fiyat tüm dirençlerin üstünde
                               (yeni zirve trendi) + RSI<70 ise engel sayılmaz
                               (02-04.07 +%3.7 ralli eski kuralla tamamen kaçmıştı)
  7b) PULLBACK tetiği       -> trend yönünde MA25 dokunuşu + doğru tarafta tutunma =
                               öncelikli giriş (90g: win %51 ort +%0.23; 02-04.07
                               replay: 2/2 hedef, 5x +%14)
  8) GECE filtresi          -> 15m planında 20-24 UTC uyarı YOK (iki pencerede en
                               kötü dilim). --no-quiet ile kapatılabilir.
  8b) AKŞAM+SICAK RSI       -> 15m LONG, 18-19 UTC + RSI>=65: giriş YOK. 90g:
                               31 işlem win %6.5 ort -%0.56 — işlem geceye sarkıp
                               ölüyor (05.07 21:45 TR stopu bu profildi).
  9) İKİNCİ GİRİŞ (reclaim) -> STOP sonrası 4 saat içinde fiyat orijinal girişi
                               geri alırsa + üst TF yön sürüyorsa + ADX>=25:
                               tekrar uyarı (test: win %48, ort +%0.22). Giriş TF
                               dizilimi ve direnç filtresi BEKLENMEZ.
  10) BREAKEVEN (sadece 15m) -> fiyat +1.5R'ye değince stop girişe çekilir.
                               90g: toplamı %+5.4'ten %+13.1'e çıkardı (n=101).
                               1h planında ZARARLI çıktı — orada kapalı.
  11) DİP KOVALAMA koruması -> RSI<=35 iken SHORT girişi YOK (08.07: 2 canlı stop;
                               90g: RSI32-45 short iki yarıda da eksi, RSI>45 artı)
Kaynak: ogrenilen_kurallar.md (walk-forward, komisyon dahil, kötümser sayım).

Her uyarıda giriş-anı özellikleri (RSI, hacim oranı, baraja mesafe, saat) journal'a
yazılır; `python -m tradebot.journal` ile veri birikince hangi koşul kazandırıyor bakılır.

Kullanım:
    python -m tradebot.copilot --symbol ETHUSDT --interval 30
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from loguru import logger

from tradebot.config import ROOT
from tradebot.datafeed import make_feed
from tradebot.indicators import adx, atr, rsi, sma
from tradebot.journal import FEE_RT_PCT, Journal

# Plan tanımları: giriş TF -> (trend TF, hedef R, min ADX, 24s S/R bar sayısı,
#                              sessiz saatler UTC)
PLANS: dict[str, dict] = {
    "15m": {"trend": "1h", "rr": 2.0, "tp_r": 0.75, "adx_min": 20.0, "sr_win": 96,
            "quiet": (20, 21, 22, 23), "be": 0.0},
    "1h":  {"trend": "1d", "rr": 3.0, "adx_min": 25.0, "sr_win": 24,
            "quiet": (), "be": 0.0},   # 1h: BE testte ZARARLI (3R'ye nefes lazım)
}
# "tp_r" (11.07): HEDEF R katı — giriş kalite filtresi rr'lik YER istemeye devam
# eder (2R alan yoksa girilmez), ama kâr 0.75R'da alınır. 90g ETH taraması
# (giriş seti aynı, sadece çıkış değişti, komisyon dahil, split-half):
#   0.75R: n=60 win %77 toplam +%9.9 (H1 +2.1 / H2 +7.8 — iki yarı da artı)
#   2.00R: n=44 win %45 toplam +%10.3 (H1 EKSİ; kaybeden 24'ün 12'si önce
#          +0.5R kârdaydı — "kârdayken stopa döndü" şikayetinin kaynağı)
# LINK 60g 0.75R: win %78 +%7.3; BTC her hedefte eksi (kadroda kullanıcı isteği).
# "be": breakeven tetiği (R katı). Hedef 0.75R'a inince BE-1.5R anlamsızlaştı
# (hedef BE tetiğinden önce gelir) — 15m'de kapatıldı. 1h planı değişmedi.


@dataclass
class Setup:
    side: str            # LONG / SHORT
    entry: float
    stop: float
    target: float
    adx: float
    reason: str
    tf: str = "15m"      # hangi plan üretti
    # giriş anı özellik fotoğrafı (öğrenme için journal'a yazılır)
    rsi: float = 0.0
    vol_ratio: float = 0.0
    room_atr: float = 0.0
    sep_pct: float = 0.0
    hour: int = 0
    # breakeven: fiyat be_at'a değince stop girişe çekilir (0 = kapalı)
    be_at: float = 0.0
    be_armed: bool = False


class Copilot:
    def __init__(
        self,
        symbol: str = "ETHUSDT",
        leverage: int = 5,
        adx_min: float = 20.0,          # 15m planı eşiği (1h planı PLANS'tan 25)
        ma_sep_min_pct: float = 0.10,   # MA7-MA25 arası min ayrım (whipsaw filtresi)
        atr_stop_mult: float = 1.5,
        vol_mult: float = 0.0,          # 0=kapalı (learner: 15m'de bu filtre ters çalıştı)
        rsi_overbought: float = 78.0,   # sadece uç blow-off koruması
        rsi_oversold: float = 22.0,
        no_quiet: bool = False,
    ):
        self.symbol = symbol
        self.leverage = leverage
        self.ma_sep_min_pct = ma_sep_min_pct
        self.atr_stop_mult = atr_stop_mult
        self.vol_mult = vol_mult
        self.rsi_overbought = rsi_overbought
        self.rsi_oversold = rsi_oversold
        self.no_quiet = no_quiet
        self.adx_overrides = {"15m": adx_min}   # CLI 15m eşiğini değiştirebilir
        self.feed = make_feed()  # futures; bloklu ortamda (GitHub) Vision spot
        self.journal = Journal()
        self.log_path = ROOT / "copilot_log.txt"
        self._reentry: dict[str, dict] = {}     # plan -> ikinci-giriş nöbeti

    # ---- kayıt ----------------------------------------------------------
    def say(self, msg: str) -> None:
        """Ekrana yazar VE copilot_log.txt'ye ekler (PC kapanırsa geçmiş kalsın)."""
        try:
            print(msg)
        except UnicodeEncodeError:  # eski Windows konsolu (cp1254) emoji basamaz
            print(msg.encode("ascii", "replace").decode("ascii"))
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"log yazılamadı: {e}")

    # ---- analiz ---------------------------------------------------------
    def _tf(self, interval: str, limit: int = 120):
        return self.feed.klines(self.symbol, interval, limit)

    def _plan_adx_min(self, tf: str) -> float:
        return self.adx_overrides.get(tf, PLANS[tf]["adx_min"])

    def analyze(self, tf: str, d_hi=None, d_lo=None, price=None,
                now=None) -> tuple[Setup | None, str]:
        """Verilen plan (giriş TF) için kural setini uygular.

        d_hi/d_lo/price/now verilirse geçmiş bir mum üzerinde çalışır —
        saatlik rapordaki "dün açsaydık ne olurdu" simülasyonu AYNI kuralları
        kullanır (kural kopyası yok, sapma yok).
        """
        p = PLANS[tf]
        if d_hi is None:
            d_hi = self._tf(p["trend"])
        if d_lo is None:
            d_lo = self._tf(tf)
        if price is None:
            price = self.feed.mark_price(self.symbol)

        # üst TF yön
        h7, h25, h99 = (sma(d_hi["close"], q).iloc[-1] for q in (7, 25, 99))
        if h7 > h25 > h99:
            bias = "LONG"
        elif h7 < h25 < h99:
            bias = "SHORT"
        else:
            return None, f"{p['trend']} trend belirsiz — BEKLE"

        # giriş TF göstergeleri
        m7, m25, m99 = (sma(d_lo["close"], q).iloc[-1] for q in (7, 25, 99))
        adx_v = float(adx(d_lo, 14).iloc[-1])
        atr_v = float(atr(d_lo, 14).iloc[-1])
        rsi_v = float(rsi(d_lo["close"], 14).iloc[-1])
        sep_pct = abs(m7 - m25) / price * 100
        # hacim: son KAPANMIŞ mum / önceki 20 mumun ortalaması (kayıt için hep hesaplanır)
        vol_avg = float(d_lo["volume"].iloc[-21:-1].mean())
        vol_ratio = float(d_lo["volume"].iloc[-2]) / vol_avg if vol_avg > 0 else 0.0
        # 24s direnç/destek — SON 2 BAR HARİÇ (yoksa yeni zirvede fiyat kendi
        # kuyruğunu kovalar, room hep 0 çıkar; 02-04.07 rallisi bu yüzden kaçtı)
        resistance = float(d_lo["high"].iloc[-p["sr_win"]:-2].max())
        support = float(d_lo["low"].iloc[-p["sr_win"]:-2].min())
        # son 2 barın dip/tepesi (pullback tetiği için)
        recent_low = float(d_lo["low"].iloc[-2:].min())
        recent_high = float(d_lo["high"].iloc[-2:].max())
        hour = (now or datetime.now(timezone.utc)).hour
        stop_dist = atr_v * self.atr_stop_mult
        target_dist = stop_dist * p["rr"]                 # YER şartı (kalite filtresi)
        tp_dist = stop_dist * p.get("tp_r", p["rr"])      # gerçek kâr-al mesafesi

        # her BEKLE satırına eklenen gerçek okuma (analiz gibi görünsün diye)
        aligned = (m7 > m25 > m99) if bias == "LONG" else (m7 < m25 < m99)
        side_ok = (price > m7) if bias == "LONG" else (price < m7)
        read = (f"{bias} ADX{adx_v:.0f} RSI{rsi_v:.0f} | "
                f"dizilim {'✓' if aligned else '✗'} fiyat-MA7 {'✓' if side_ok else '✗'}")

        # --- Kalite filtreleri ---
        if not self.no_quiet and hour in p["quiet"]:
            return None, (f"{read} · gece {(hour + 3) % 24:02d}:00 TR — 90g testte bu "
                          f"dilim 15m'de zararlı (86 işlem, win %26, ort -%0.19) — "
                          f"15m uyarısı kapalı, 1h planı gece de açık")
        if adx_v < self._plan_adx_min(tf):
            return None, f"{read} · ADX zayıf<{self._plan_adx_min(tf):.0f} (choppy) — BEKLE"
        if sep_pct < self.ma_sep_min_pct:
            return None, f"{read} · MA'lar yapışık %{sep_pct:.2f} (whipsaw) — BEKLE"
        if self.vol_mult > 0 and vol_ratio < self.vol_mult:  # varsayılan kapalı
            return None, f"{read} · hacim zayıf {vol_ratio:.2f}x — BEKLE"
        # TEPE KOVALAMA koruması (10.07: tarayıcıda 4 sinyal 4 stop — hepsi 15m
        # LONG RSI65-69, saatler farklı; sorun saat değil RSI'mış). Üç coinde
        # aynı yön: ETH 90g LONG RSI>=65 n=7 win %14 / RSI<65 win %50;
        # BTC 60g n=3 ve LINK 60g n=5 hepsi stop. Eski 18-19 UTC akşam
        # filtresinin genel hâli — onu kapsar, yerine geçti.
        if tf == "15m" and bias == "LONG" and rsi_v >= 65:
            return None, (f"{read} · RSI{rsi_v:.0f} zaten sıcak — LONG şimdi girmek "
                          f"tepe kovalamak olur (3 coin 15 işlem: 1 kazanç) — BEKLE")
        # DİP KOVALAMA koruması (08.07: RSI 27 ve 30'da iki SHORT, ikisi de stop,
        # ilki 5 dakikada). 90g: RSI32-45 SHORT iki yarıda da eksi (-0.20/-0.20),
        # RSI>45 SHORT iki yarıda da artı. Kanıt orta kuvvette (n küçük) ama
        # canlı kayıplarla aynı yönde — taban 35.
        if bias == "SHORT" and rsi_v <= 35:
            return None, (f"{read} · RSI{rsi_v:.0f} zaten dipte — SHORT şimdi girmek "
                          f"dip kovalamak olur (düşüş RSI>45'ken yakalanır) — BEKLE")

        def mk(side: str, room: float, tag: str) -> Setup:
            stop = price - stop_dist if side == "LONG" else price + stop_dist
            target = price + tp_dist if side == "LONG" else price - tp_dist
            be = p.get("be", 0.0)
            be_at = (price + stop_dist * be if side == "LONG"
                     else price - stop_dist * be) if be else 0.0
            return Setup(side, price, stop, target, adx_v,
                         f"[{tf}] {tag} + {p['trend']} trend + ADX{adx_v:.0f} "
                         f"vol{vol_ratio:.1f}x RSI{rsi_v:.0f} yer{room / atr_v:.1f}ATR "
                         f"(hedef {p.get('tp_r', p['rr']):.2g}R)",
                         tf=tf, rsi=rsi_v, vol_ratio=vol_ratio,
                         room_atr=room / atr_v if atr_v else 0.0,
                         sep_pct=sep_pct, hour=hour, be_at=be_at)

        open_sky_up = price > resistance     # tüm dirençlerin üstü
        open_sky_dn = price < support        # tüm desteklerin altı

        # --- TETİK 1: PULLBACK (öncelikli — 90g test: win %51, ort +%0.23) ---
        # Trend yönünde MA25'e dokunup doğru tarafta tutunma = ucuz giriş
        if bias == "LONG" and m7 > m25 and recent_low <= m25 and price > m25 and rsi_v < 75:
            room = resistance - price
            if open_sky_up or room >= target_dist:
                return mk("LONG", room, "PULLBACK(MA25 dokunuş)"), "KURULUM VAR"
        if bias == "SHORT" and m7 < m25 and recent_high >= m25 and price < m25 and rsi_v > 25:
            room = price - support
            if open_sky_dn or room >= target_dist:
                return mk("SHORT", room, "PULLBACK(MA25 dokunuş)"), "KURULUM VAR"

        # --- TETİK 2: TREND-TAKİP ---
        if bias == "LONG" and aligned and side_ok:
            if rsi_v >= self.rsi_overbought:
                return None, f"{read} · RSI{rsi_v:.0f} uç aşırı-alım (tepe kovalama) — BEKLE"
            room = resistance - price
            if open_sky_up:
                if rsi_v >= 70:  # açık gökyüzü ama nefessiz (03.07: RSI73 girişler stop yedi)
                    return None, f"{read} · üstü açık ama RSI{rsi_v:.0f} sıcak — pullback bekle"
                return mk("LONG", room, "KIRILIM(üstü açık)"), "KURULUM VAR"
            if room < target_dist:
                return None, (f"{read} · dirence yapışık (yer {room:.0f}$ < gereken "
                              f"{target_dist:.0f}$, zirve {resistance:.0f}) — BEKLE")
            return mk("LONG", room, "TREND"), "KURULUM VAR"
        if bias == "SHORT" and aligned and side_ok:
            if rsi_v <= self.rsi_oversold:
                return None, f"{read} · RSI{rsi_v:.0f} uç aşırı-satım (dip kovalama) — BEKLE"
            room = price - support
            if open_sky_dn:
                if rsi_v <= 30:
                    return None, f"{read} · altı açık ama RSI{rsi_v:.0f} tükenik — pullback bekle"
                return mk("SHORT", room, "KIRILIM(altı açık)"), "KURULUM VAR"
            if room < target_dist:
                return None, (f"{read} · desteğe yapışık (yer {room:.0f}$ < gereken "
                              f"{target_dist:.0f}$, dip {support:.0f}) — BEKLE")
            return mk("SHORT", room, "TREND"), "KURULUM VAR"

        # yön var ama giriş TF henüz hizalanmadı — neyin eksik olduğunu söyle
        eksik = "dizilim bozuk" if not aligned else f"fiyat MA7'nin ters tarafında ({m7:.0f})"
        return None, f"{read} · {tf} tetik yok ({eksik}) — BEKLE"

    def analyze_reentry(self, tf: str, price: float) -> Setup | None:
        """STOP sonrası reclaim (ikinci giriş) kontrolü — plan bazında.

        Test (90g): stopların %42'sinde reclaim oluşuyor; ADX>=25 + gece hariç
        ikinci girişler win %47.6, ort +%0.22. Giriş TF dizilimi ve direnç
        filtresi bilerek atlanır (V-dönüşte MA'lar geç kalır — 02.07 canlı örnek).
        """
        ra = self._reentry.get(tf)
        if not ra:
            return None
        now = datetime.now(timezone.utc)
        p = PLANS[tf]
        if now > ra["until"]:
            self._reentry.pop(tf, None)
            return None
        if not self.no_quiet and now.hour in p["quiet"]:
            return None
        side = ra["side"]
        reclaim = price > ra["entry"] if side == "LONG" else price < ra["entry"]
        if not reclaim:
            return None
        d_hi = self._tf(p["trend"])
        d_lo = self._tf(tf)
        h7, h25, h99 = (sma(d_hi["close"], q).iloc[-1] for q in (7, 25, 99))
        bias_ok = h7 > h25 > h99 if side == "LONG" else h7 < h25 < h99
        adx_v = float(adx(d_lo, 14).iloc[-1])
        if not bias_ok or adx_v < 25:
            return None
        atr_v = float(atr(d_lo, 14).iloc[-1])
        rsi_v = float(rsi(d_lo["close"], 14).iloc[-1])
        m7, m25 = (sma(d_lo["close"], q).iloc[-1] for q in (7, 25))
        stop_dist = atr_v * self.atr_stop_mult
        stop = price - stop_dist if side == "LONG" else price + stop_dist
        tp_dist = stop_dist * p.get("tp_r", p["rr"])
        target = price + tp_dist if side == "LONG" else price - tp_dist
        self._reentry.pop(tf, None)
        be = p.get("be", 0.0)
        be_at = (price + stop_dist * be if side == "LONG"
                 else price - stop_dist * be) if be else 0.0
        return Setup(side, price, stop, target, adx_v,
                     f"[{tf}] İKİNCİ GİRİŞ: stop avı sonrası {ra['entry']:.2f} geri "
                     f"alındı, {p['trend']} yön sürüyor, ADX{adx_v:.0f}",
                     tf=tf, rsi=rsi_v, vol_ratio=0.0, room_atr=0.0,
                     sep_pct=abs(m7 - m25) / price * 100, hour=now.hour, be_at=be_at)

    # ---- döngü ----------------------------------------------------------
    def run(self, interval: int = 30) -> None:
        logger.info(f"Co-pilot başladı: {self.symbol} | her {interval}s | kaldıraç {self.leverage}x")
        logger.info("Yol gösterme modu — gerçek emir AÇILMAZ. Ctrl+C ile durdur.")
        self.say(f"\n===== OTURUM BAŞLADI {datetime.now():%Y-%m-%d %H:%M:%S} | "
                 f"{self.symbol} | planlar: " +
                 ", ".join(f"[{tf}->{p['trend']} hedef {p.get('tp_r', p['rr']):.2g}R]"
                           for tf, p in PLANS.items()) +
                 f" | her {interval}s | kaldıraç {self.leverage}x =====")
        active: dict[str, tuple[Setup, int]] = {}   # tf -> (setup, alert_id)

        while True:
            try:
                price = self.feed.mark_price(self.symbol)
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                segments: list[str] = []

                for tf in PLANS:
                    st = active.get(tf)
                    if st is None:
                        setup = self.analyze_reentry(tf, price)
                        status = "İKİNCİ GİRİŞ" if setup else ""
                        if setup is None:
                            setup, status = self.analyze(tf)
                        if setup:
                            aid = self.journal.add(
                                self.symbol, setup.side, setup.entry, setup.stop,
                                setup.target, setup.adx, setup.reason,
                                rsi=setup.rsi, vol_ratio=setup.vol_ratio,
                                room_atr=setup.room_atr, sep_pct=setup.sep_pct,
                                hour=setup.hour)
                            active[tf] = (setup, aid)
                            self._print_alert(setup)
                            segments.append(f"{tf}:🔔{setup.side}")
                        else:
                            segments.append(f"{tf}: {status}")
                    else:
                        a, aid = st
                        chg = (price - a.entry) / a.entry * 100 * (1 if a.side == "LONG" else -1)
                        # breakeven: +be R'ye değdiyse stop girişe çekilir
                        if a.be_at and not a.be_armed and (
                                price >= a.be_at if a.side == "LONG" else price <= a.be_at):
                            a.be_armed = True
                            a.stop = a.entry
                            self.say(f"{ts}  >>> [{tf}] BREAKEVEN: {a.be_at:.2f} görüldü — "
                                     f"STOP girişe ({a.entry:.2f}) çekildi. Gerçek emrinde sen de çek!")
                        hit = None
                        if a.side == "LONG":
                            if price <= a.stop: hit = "BE" if a.be_armed else "STOP"
                            elif price >= a.target: hit = "HEDEF"
                        else:
                            if price >= a.stop: hit = "BE" if a.be_armed else "STOP"
                            elif price <= a.target: hit = "HEDEF"
                        if hit:
                            self.journal.close(aid, hit, round(chg, 3))
                            net = chg - FEE_RT_PCT
                            self.say(f"{ts}  >>> [{tf}] {hit}!  {a.side} {a.entry:.2f} -> "
                                     f"{price:.2f} ({chg:+.2f}% ham | komisyonla "
                                     f"{net:+.2f}% x{self.leverage} = "
                                     f"{net * self.leverage:+.2f}%)")
                            s = self.journal.summary()
                            self.say(f"     journal: {s['kapanan']} kapanan, win %{s['win_rate']}, "
                                     f"toplam {s['toplam_pnl_pct']:+.2f}%, "
                                     f"komisyonla {s['toplam_net_pct']:+.2f}%")
                            if hit == "STOP":  # stop avı olabilir — reclaim nöbeti (BE değil)
                                self._reentry[tf] = {
                                    "side": a.side, "entry": a.entry,
                                    "until": datetime.now(timezone.utc) + timedelta(hours=4)}
                                self.say(f"     ([{tf}] ikinci-giriş nöbeti: 4 saat içinde "
                                         f"{a.entry:.2f} geri alınırsa tekrar uyarırım)")
                            active.pop(tf, None)
                            segments.append(f"{tf}: {hit} sonrası bekleniyor")
                        else:
                            segments.append(f"{tf}: AÇIK {a.side} ({chg:+.2f}%) "
                                            f"stop {a.stop:.2f} hedef {a.target:.2f}")

                self.say(f"{ts}  {self.symbol} {price:.2f}  ·  " + "  |  ".join(segments))
                time.sleep(interval)
            except KeyboardInterrupt:
                durum = ", ".join(f"{tf}:AÇIK" for tf in active) or "FLAT"
                self.say(f"===== OTURUM DURDURULDU {datetime.now():%Y-%m-%d %H:%M:%S} "
                         f"| son durum: {durum} =====\n")
                break
            except Exception as e:  # noqa: BLE001
                logger.warning(f"tick hatası: {e}")
                time.sleep(interval)

    def _print_alert(self, s: Setup) -> None:
        p = PLANS[s.tf]
        rr = p.get("tp_r", p["rr"])
        self.say("\n" + "=" * 60)
        self.say(f"  🔔 KURULUM [{s.tf}]: {s.side}")
        self.say(f"  Giriş : {s.entry:.2f}")
        self.say(f"  Stop  : {s.stop:.2f}")
        self.say(f"  Hedef : {s.target:.2f}  ({rr:.2g}R)")
        self.say(f"  Teyit : RSI {s.rsi:.0f} · hacim {s.vol_ratio:.1f}x · "
                 f"baraj {s.room_atr:.1f} ATR uzakta")
        if s.be_at:
            self.say(f"  BE    : fiyat {s.be_at:.2f} olursa STOP'u girişe çek "
                     f"(90g test: toplam kârı 2x'ledi)")
        self.say(f"  Neden : {s.reason}")
        self.say(f"  (Emri ve STOP'u Binance'e SEN koy. Kaldıraç {self.leverage}x.)")
        self.say("=" * 60 + "\n")


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # emoji/özel karakter için
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="Canlı Uyarı Co-pilot (çift plan: 15m + 1h)")
    ap.add_argument("--symbol", default="ETHUSDT")
    ap.add_argument("--interval", type=int, default=30, help="kontrol aralığı (saniye)")
    ap.add_argument("--adx-min", type=float, default=20.0, help="15m planı ADX eşiği")
    ap.add_argument("--ma-sep", type=float, default=0.10, help="min MA ayrımı %%")
    ap.add_argument("--vol-mult", type=float, default=0.0,
                    help="min hacim oranı (0=kapalı; learner 15m'de ters buldu)")
    ap.add_argument("--no-quiet", action="store_true",
                    help="gece filtresini (15m: 20-24 UTC uyarı yok) kapat")
    args = ap.parse_args()

    cp = Copilot(symbol=args.symbol, adx_min=args.adx_min, ma_sep_min_pct=args.ma_sep,
                 vol_mult=args.vol_mult, no_quiet=args.no_quiet)
    cp.run(interval=args.interval)


if __name__ == "__main__":
    main()
