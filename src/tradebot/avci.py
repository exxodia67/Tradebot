"""Fırsat Avcısı — 13 pattern'i CANLI tarar, RUHSATLI olanları anında bildirir.

Copilot'tan farkı: copilot 2-3 sıkı kurulum bilir (pullback/trend/kırılım);
avcı, öğrenme motorunun 13 pattern'inin TAMAMINI izler ama sadece KANITLA
RUHSAT ALMIŞ olanları bildirir.

Ruhsat (12 saatte bir, coin başına 90 gün walk-forward, komisyon dahil,
çıkışlar canlıyla aynı: ATR×1.5 stop, 0.75R hedef):
  * en az 20 işlem                       (azı fikirdir, kanıt değil)
  * ortalama işlem kârı > 0              (komisyon düşülmüş)
  * dönemin İKİ YARISINDA da ortalama > 0 (tek döneme yaslanan = şans)
Ruhsatı geçemeyen pattern o coin'de SUSAR. BTC'de hiçbir pattern geçemezse
BTC'den hiç ses çıkmaz — bu hata değil, dürüstlüktür.

Canlı tarama: her ~60 sn, SADECE yeni KAPANAN 15m barında sinyal arar
(oluşan barda karar yok — jilet kenarı girişler 10.07'de canımızı yaktı).
Coin başına tek açık kâğıt işlem; stop yiyince 90 dk soğuma.

ÖNEMLİ: Telegram'a SADECE MESAJ GÖNDERİR — komut dinlemez (ana botla çakışmaz).
GERÇEK EMİR AÇMAZ — kâğıt işlem, karne avci_journal.db'de birikir.

Kullanım: normalde telegram_bot içinde otomatik başlar (TEK UYGULAMA).
Tek başına: python -m tradebot.avci
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
from loguru import logger

from tradebot.config import STATE_DIR, Secrets
from tradebot.copilot import Setup
from tradebot.exchange.binance_futures import fetch_mainnet_klines
from tradebot.journal import Journal
from tradebot.learner import add_higher_trend, prep, signals, walk_forward
from tradebot.ogrenme_daemon import PAT_TR
from tradebot.tarayici import fmt

COINS = ("ETHUSDT", "BTCUSDT", "LINKUSDT")
TF, TREND_TF, SR_WIN, MAX_HOLD = "15m", "1h", 96, 96
RUHSAT_GUN = 90
RUHSAT_SN = 12 * 3600      # ruhsat tazeleme aralığı
MIN_N = 20                 # ruhsat için asgari işlem sayısı
TARAMA_SN = 60
OZET_SN = 3600
SOGUMA_DK = 90             # stop sonrası
CHAT_FILE = STATE_DIR / "telegram_chat.json"
STATE_FILE = STATE_DIR / "avci_state.json"
DB_FILE = STATE_DIR / "avci_journal.db"


class Avci:
    def __init__(self):
        self.token = Secrets().telegram_bot_token
        if not self.token:
            print("HATA: .env içinde TELEGRAM_BOT_TOKEN yok.")
            sys.exit(1)
        try:
            self.chat_id = json.loads(CHAT_FILE.read_text())["chat_id"]
        except Exception:  # noqa: BLE001
            print("HATA: telegram_chat.json yok — önce ana bota /start yazılmalı.")
            sys.exit(1)
        self.journal = Journal(DB_FILE)
        # (sym, pattern, side) -> (n, win%, ort%) — ruhsatlı kombolar
        self.ruhsat: dict[tuple[str, str, str], tuple[int, float, float]] = {}
        self.ruhsat_ts = 0.0
        self.active: dict[str, tuple[Setup, int, str]] = {}   # sym -> (setup, aid, ts)
        self.cooldown: dict[str, datetime] = {}               # sym -> kadar
        self.son_bar: dict[str, object] = {}                  # sym -> işlenen son bar
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

    # ---- durum ---------------------------------------------------------------
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

    # ---- ruhsat: hangi pattern hangi coin'de konuşabilir ---------------------
    def ruhsat_yenile(self) -> None:
        logger.info("ruhsat taraması başladı (coin başına 90g walk-forward)...")
        yeni: dict = {}
        for sym in COINS:
            try:
                edf = fetch_mainnet_klines(sym, TF, RUHSAT_GUN)
                tdf = fetch_mainnet_klines(sym, TREND_TF, RUHSAT_GUN)
                d = add_higher_trend(prep(edf, SR_WIN), prep(tdf, 24))
                trades = walk_forward(d, max_hold=MAX_HOLD, rr=0.75)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"{sym} ruhsat taraması hatası: {e}")
                continue
            grup: dict = {}
            for t in trades:
                grup.setdefault((t.pattern, t.side), []).append(t)
            for (pat, side), g in grup.items():
                if len(g) < MIN_N:
                    continue
                h = len(g) // 2
                o1 = sum(x.pnl_pct for x in g[:h]) / h
                o2 = sum(x.pnl_pct for x in g[h:]) / (len(g) - h)
                ort = sum(x.pnl_pct for x in g) / len(g)
                if ort > 0 and o1 > 0 and o2 > 0:
                    win = sum(x.pnl_pct > 0 for x in g) / len(g) * 100
                    yeni[(sym, pat, side)] = (len(g), win, ort)
        self.ruhsat = yeni
        self.ruhsat_ts = time.time()
        logger.info(f"ruhsat bitti: {len(yeni)} kombo geçti")

    def ruhsat_ozet(self) -> str:
        if not self.ruhsat:
            return "Ruhsatlı pattern YOK — hiçbir kombo kanıt eşiğini geçemedi, avcı susuyor."
        lines = []
        for (sym, pat, side), (n, w, o) in sorted(
                self.ruhsat.items(), key=lambda kv: -kv[1][2]):
            lines.append(f"  {sym[:-4]} {PAT_TR.get(pat, pat)} {side} — "
                         f"{n} işlemde win %{w:.0f}, ort %{o:+.2f}")
        return "Ruhsatlı kombolar (90g kanıtlı):\n" + "\n".join(lines)

    # ---- kapanış taraması (5m, kötümser) -------------------------------------
    def _resolve_hit(self, sym: str, a: Setup, ats: str):
        since = datetime.fromisoformat(ats)
        mins = (datetime.now(timezone.utc) - since).total_seconds() / 60
        limit = min(1000, max(3, int(mins / 5) + 3))
        d = fetch_mainnet_klines(sym, "5m", max(1, int(mins / 1440) + 1)).tail(limit)
        for _, row in d.iterrows():
            if row["open_time"] < since:
                continue
            if a.side == "LONG":
                if row["low"] <= a.stop:
                    return "STOP", a.stop, row["open_time"]
                if row["high"] >= a.target:
                    return "HEDEF", a.target, row["open_time"]
            else:
                if row["high"] >= a.stop:
                    return "STOP", a.stop, row["open_time"]
                if row["low"] <= a.target:
                    return "HEDEF", a.target, row["open_time"]
        return None, None, None

    # ---- tek coin taraması ----------------------------------------------------
    def _coin_tick(self, sym: str) -> None:
        st = self.active.get(sym)
        if st is not None:                       # açık işlem: kapanış ara
            a, aid, ats = st
            hit, exit_px, hit_t = self._resolve_hit(sym, a, ats)
            if not hit:
                return
            chg = ((exit_px - a.entry) / a.entry * 100
                   * (1 if a.side == "LONG" else -1))
            self.journal.close(aid, hit, round(chg, 3))
            em = "✅" if hit == "HEDEF" else "🛑"
            ek = ""
            if hit == "STOP":
                self.cooldown[sym] = (datetime.now(timezone.utc)
                                      + timedelta(minutes=SOGUMA_DK))
                ek = f"\n({sym} {SOGUMA_DK} dk soğumada)"
            s = self.journal.summary()
            saat = (hit_t + timedelta(hours=3)).strftime("%H:%M")
            self.send(f"{em} 🎯 {sym} {hit}: {a.side} {fmt(a.entry)} -> "
                      f"{fmt(exit_px)} ({chg:+.2f}% | 5x {chg * 5:+.2f}%) {saat} TR\n"
                      f"Avcı karnesi: {s['kapanan']} kapanan, win %{s['win_rate']}, "
                      f"toplam %{s['toplam_pnl_pct']}{ek}")
            self.active.pop(sym, None)
            return
        cd = self.cooldown.get(sym)
        if cd and datetime.now(timezone.utc) < cd:
            return
        # yeni KAPANMIŞ bar var mı? (oluşan barda karar yok)
        edf = fetch_mainnet_klines(sym, TF, 3)
        tdf = fetch_mainnet_klines(sym, TREND_TF, 6)
        d = add_higher_trend(prep(edf, SR_WIN), prep(tdf, 24))
        rows = list(d.itertuples(index=False))
        if len(rows) < 4:
            return
        r, prev, prev2 = rows[-2], rows[-3], rows[-4]   # -1 = oluşan bar
        if self.son_bar.get(sym) == r.open_time:
            return
        self.son_bar[sym] = r.open_time
        vals = (r.ma99, r.h99, r.adx14, r.atr14, r.rsi14, r.vol_ratio, r.res, r.sup)
        if any(pd.isna(v) for v in vals) or r.atr14 <= 0:
            return
        for pat, side in signals(r, prev, prev2):
            ruhsat = self.ruhsat.get((sym, pat, side))
            if not ruhsat:
                continue
            n, w, ort = ruhsat
            entry = float(r.close)
            sd = float(r.atr14) * 1.5
            stop = entry - sd if side == "LONG" else entry + sd
            target = entry + sd * 0.75 if side == "LONG" else entry - sd * 0.75
            reason = (f"[{TF}] {PAT_TR.get(pat, pat)} — 90g ruhsat: {n} işlemde "
                      f"win %{w:.0f}, ort %{ort:+.2f} (0.75R hedef)")
            aid = self.journal.add(sym, side, entry, stop, target,
                                   float(r.adx14), reason, rsi=float(r.rsi14),
                                   vol_ratio=float(r.vol_ratio or 0),
                                   room_atr=0.0, sep_pct=0.0,
                                   hour=r.open_time.hour)
            self.active[sym] = (Setup(side, entry, stop, target, float(r.adx14),
                                      reason, tf=TF),
                                aid, datetime.now(timezone.utc).isoformat())
            saat = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%H:%M")
            self.send(f"🎯 FIRSAT {sym} {side}  ({saat} TR)\n"
                      f"Giriş: {fmt(entry)}  Stop: {fmt(stop)}  Hedef: {fmt(target)}\n"
                      f"{reason}\n(Kâğıt işlem — emir istersen SEN aç, 5x)\n"
                      f"Grafik: https://www.tradingview.com/chart/?symbol="
                      f"BINANCE:{sym}.P&interval=15")
            logger.info(f"fırsat: {sym} {pat} {side} @{entry}")
            break   # coin başına tek işlem

    # ---- saatlik özet ---------------------------------------------------------
    def ozet(self) -> str:
        saat = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%H:%M")
        lines = [f"🎯 Avcı özeti {saat} TR — {len(COINS)} coin, "
                 f"{len(self.ruhsat)} ruhsatlı kombo"]
        for sym, (a, _aid, _ats) in self.active.items():
            lines.append(f"📌 {sym} {a.side} @{fmt(a.entry)} açık")
        s = self.journal.summary()
        if s["kapanan"]:
            lines.append(f"📒 Karne: {s['kapanan']} kapanan, win %{s['win_rate']}, "
                         f"toplam %{s['toplam_pnl_pct']}")
        if not self.active and not s["kapanan"]:
            lines.append("📌 Henüz işlem yok — ruhsatlı pattern'ler bar kapanışı bekliyor.")
        return "\n".join(lines)

    # ---- ana döngü ------------------------------------------------------------
    def run(self) -> None:
        self.ruhsat_yenile()
        logger.info(f"Avcı başladı: {', '.join(COINS)}")
        if self.banner:
            self.send(f"🎯 Fırsat Avcısı başladı — {', '.join(c[:-4] for c in COINS)}, "
                      f"13 pattern, sadece kanıtlılar konuşur.\n{self.ruhsat_ozet()}\n"
                      f"Not: ruhsat 12 saatte bir tazelenir; kanıtı düşen pattern susar.")
        son_ozet = time.time()
        while True:
            t0 = time.time()
            if time.time() - self.ruhsat_ts >= RUHSAT_SN:
                try:
                    self.ruhsat_yenile()
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"ruhsat yenileme hatası: {e}")
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
    Avci().run()


if __name__ == "__main__":
    main()
