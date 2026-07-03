"""Öğrenme Motoru — geçmiş veriyi İLERİYİ GÖRMEDEN (walk-forward) test eder.

Ne yapar:
  1) Mainnet'ten GERÇEK geçmiş mumları çeker (5m, 15m, 1h + 1d bağlam)
  2) Her barda SADECE o ana kadarki veriyle 10 pattern arar:
       P1_TREND     : üst TF trend + MA7/25/99 dizili + fiyat MA7'nin doğru tarafında
       P2_PULLBACK  : trend yönünde MA25'e geri çekilme + doğru tarafta kapanış
       P3_BREAKOUT  : hacimle (>=1.2x) 20-bar zirve/dip kırılımı, trend yönünde
       P4_KESISIM   : MA7'nin MA25'i trend yönünde kesmesi (MA99 teyitli)
       P5_RSI_DONUS : trend içinde RSI'ın 35/65 bölgesinden geri dönmesi
       P6_YUTAN     : yutan mum (engulfing) trend yönünde
       P7_PINBAR    : pin bar / iğne (fitil reddi) trend yönünde
       P8_SIKISMA   : Bollinger sıkışması sonrası kırılım, trend yönünde
       P9_SEKME_MR  : 24s destek/dirençten sekme — TREND FİLTRESİZ (mean-rev testi)
       P10_ICBAR    : iç bar (inside bar) kırılımı, trend yönünde
  3) Her sinyali ATR-stop / R-hedefle sonucuna kadar izler ve puanlar.
     DÜRÜSTLÜK: aynı barda stop+hedef ikisi de vurulmuşsa STOP sayılır (kötümser),
     her işlemden %0.08 gidiş-dönüş komisyon düşülür.
  4) Sonuçları koşullara kırar (ADX, hacim, RSI, saat, gün, volatilite, trend yaşı,
     momentum, baraja mesafe) + çıkış formüllerini (stop x hedef) yarıştırır
     -> ogrenilen_kurallar.md

NOT: Geçmişte çalışan gelecekte çalışmayabilir. n<30 olan kovalar FİKİRDİR, kanıt değil.

Kullanım:
    python -m tradebot.learner --symbol ETHUSDT --days 90
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from tradebot.config import ROOT
from tradebot.exchange.binance_futures import fetch_mainnet_klines
from tradebot.indicators import adx, atr, rsi, sma

FEE_RT_PCT = 0.08   # gidiş-dönüş taker komisyonu (%): 2 x %0.04
WEEKDAYS = ("Pzt", "Sal", "Car", "Per", "Cum", "Cmt", "Paz")
EXIT_COMBOS = ((1.0, 1.5), (1.0, 2.0), (1.5, 2.0), (1.5, 3.0), (2.0, 2.0))


@dataclass
class TradeRes:
    ts: object          # giriş zamanı (Timestamp)
    pattern: str
    side: str           # LONG / SHORT
    outcome: str        # HEDEF / STOP / ZAMAN
    pnl_pct: float      # komisyon düşülmüş
    adx: float
    vol_ratio: float
    rsi: float
    hour: int           # UTC
    room_atr: float     # 24s direnç/desteğe mesafe (ATR cinsinden)
    sep_pct: float      # MA7-MA25 ayrımı %
    weekday: int = 0    # 0=Pzt
    atr_pct: float = 0.0    # ATR / fiyat % (volatilite rejimi)
    trend_age: int = 0      # kaç bardır MA dizilimi bozulmadan sürüyor
    streak3: bool = False   # son 3 mum aynı yönde mi (momentum)


# ---- hazırlık (hepsi GERİYE bakan pencereler — ileri sızıntı yok) ----------
def prep(df: pd.DataFrame, sr_win: int) -> pd.DataFrame:
    out = df.copy()
    for p in (7, 25, 99):
        out[f"ma{p}"] = sma(out["close"], p)
    out["adx14"] = adx(out, 14)
    out["atr14"] = atr(out, 14)
    out["rsi14"] = rsi(out["close"], 14)
    out["vol_ratio"] = out["volume"] / out["volume"].rolling(20).mean().shift(1)
    out["res"] = out["high"].shift(1).rolling(sr_win).max()   # önceki 24s zirvesi
    out["sup"] = out["low"].shift(1).rolling(sr_win).min()    # önceki 24s dibi
    out["hi20"] = out["high"].shift(1).rolling(20).max()
    out["lo20"] = out["low"].shift(1).rolling(20).min()
    # trend yaşı: dizilim kaç bardır sürüyor
    up = (out["ma7"] > out["ma25"]) & (out["ma25"] > out["ma99"])
    dn = (out["ma7"] < out["ma25"]) & (out["ma25"] < out["ma99"])
    out["up_age"] = up.astype(int).groupby((~up).cumsum()).cumsum()
    out["dn_age"] = dn.astype(int).groupby((~dn).cumsum()).cumsum()
    # Bollinger sıkışması: bant genişliği son 60 barın en dar %25'lik dilimindeyse
    mid = out["close"].rolling(20).mean()
    std = out["close"].rolling(20).std()
    bw = (4 * std) / mid * 100
    out["squeeze"] = bw <= bw.shift(1).rolling(60).quantile(0.25)
    return out


def add_higher_trend(d: pd.DataFrame, higher_prepped: pd.DataFrame) -> pd.DataFrame:
    """Üst TF MA'larını, SADECE kapanmış üst-TF barından (backward asof) ekler."""
    h = higher_prepped[["close_time", "ma7", "ma25", "ma99"]].rename(
        columns={"ma7": "h7", "ma25": "h25", "ma99": "h99"})
    return pd.merge_asof(d, h, on="close_time", direction="backward")


# ---- pattern sinyalleri (bar kapanışında karar) ----------------------------
def signals(r, prev, prev2) -> list[tuple[str, str]]:
    out = []
    up = r.h7 > r.h25 > r.h99
    dn = r.h7 < r.h25 < r.h99
    body = abs(r.close - r.open)
    up_wick = r.high - max(r.close, r.open)
    dn_wick = min(r.close, r.open) - r.low

    # P1 trend-takip
    if up and r.ma7 > r.ma25 > r.ma99 and r.close > r.ma7:
        out.append(("P1_TREND", "LONG"))
    if dn and r.ma7 < r.ma25 < r.ma99 and r.close < r.ma7:
        out.append(("P1_TREND", "SHORT"))
    # P2 pullback
    if up and r.ma7 > r.ma25 and r.low <= r.ma25 and r.close > r.ma25:
        out.append(("P2_PULLBACK", "LONG"))
    if dn and r.ma7 < r.ma25 and r.high >= r.ma25 and r.close < r.ma25:
        out.append(("P2_PULLBACK", "SHORT"))
    # P3 hacimli kırılım
    if up and r.close > r.hi20 and r.vol_ratio >= 1.2:
        out.append(("P3_BREAKOUT", "LONG"))
    if dn and r.close < r.lo20 and r.vol_ratio >= 1.2:
        out.append(("P3_BREAKOUT", "SHORT"))
    # P4 MA kesişimi
    if prev is not None:
        if up and prev.ma7 <= prev.ma25 and r.ma7 > r.ma25 and r.close > r.ma99:
            out.append(("P4_KESISIM", "LONG"))
        if dn and prev.ma7 >= prev.ma25 and r.ma7 < r.ma25 and r.close < r.ma99:
            out.append(("P4_KESISIM", "SHORT"))
        # P5 RSI dönüşü (trend içinde ucuzlama/pahalanmadan toparlanma)
        if up and prev.rsi14 < 35 <= r.rsi14:
            out.append(("P5_RSI_DONUS", "LONG"))
        if dn and prev.rsi14 > 65 >= r.rsi14:
            out.append(("P5_RSI_DONUS", "SHORT"))
        # P6 yutan mum
        if up and prev.close < prev.open and r.close > r.open \
                and r.close >= prev.open and r.open <= prev.close:
            out.append(("P6_YUTAN", "LONG"))
        if dn and prev.close > prev.open and r.close < r.open \
                and r.close <= prev.open and r.open >= prev.close:
            out.append(("P6_YUTAN", "SHORT"))
        # P8 sıkışma kırılımı (önceki bar sıkışıktı, bu bar kırdı)
        if up and bool(prev.squeeze) and r.close > r.hi20:
            out.append(("P8_SIKISMA", "LONG"))
        if dn and bool(prev.squeeze) and r.close < r.lo20:
            out.append(("P8_SIKISMA", "SHORT"))
    # P7 pin bar (fitil reddi)
    if up and body > 0 and dn_wick >= 2 * body and up_wick <= body:
        out.append(("P7_PINBAR", "LONG"))
    if dn and body > 0 and up_wick >= 2 * body and dn_wick <= body:
        out.append(("P7_PINBAR", "SHORT"))
    # P9 destek/direnç sekmesi — MEAN REVERSION, bilerek trend filtresiz
    if r.low <= r.sup and r.close > r.sup:
        out.append(("P9_SEKME_MR", "LONG"))
    if r.high >= r.res and r.close < r.res:
        out.append(("P9_SEKME_MR", "SHORT"))
    # P10 iç bar kırılımı
    if prev is not None and prev2 is not None \
            and prev.high < prev2.high and prev.low > prev2.low:
        if up and r.close > prev2.high:
            out.append(("P10_ICBAR", "LONG"))
        if dn and r.close < prev2.low:
            out.append(("P10_ICBAR", "SHORT"))
    return out


# ---- işlem simülasyonu ------------------------------------------------------
def simulate(rows, i: int, side: str, entry: float, stop_d: float,
             rr: float, max_hold: int) -> tuple[str, float, int]:
    """Girişten sonra bar bar ilerler. Aynı barda stop+hedef vurulursa STOP (kötümser)."""
    stop = entry - stop_d if side == "LONG" else entry + stop_d
    target = entry + stop_d * rr if side == "LONG" else entry - stop_d * rr
    for j in range(i + 1, min(i + 1 + max_hold, len(rows))):
        b = rows[j]
        if side == "LONG":
            if b.low <= stop:
                return "STOP", (stop - entry) / entry * 100 - FEE_RT_PCT, j
            if b.high >= target:
                return "HEDEF", (target - entry) / entry * 100 - FEE_RT_PCT, j
        else:
            if b.high >= stop:
                return "STOP", (entry - stop) / entry * 100 - FEE_RT_PCT, j
            if b.low <= target:
                return "HEDEF", (entry - target) / entry * 100 - FEE_RT_PCT, j
    j = min(i + max_hold, len(rows) - 1)
    b = rows[j]
    pnl = (b.close - entry) / entry * 100 * (1 if side == "LONG" else -1) - FEE_RT_PCT
    return "ZAMAN", pnl, j


def walk_forward(d: pd.DataFrame, max_hold: int, warmup: int = 120,
                 atr_mult: float = 1.5, rr: float = 2.0) -> list[TradeRes]:
    """Bar bar ilerler; her karar yalnızca o bara kadarki veriyle verilir."""
    rows = list(d.itertuples(index=False))
    results: list[TradeRes] = []
    busy: dict[str, int] = {}   # pattern -> bu indexe kadar işlemde (üst üste binme yok)
    for i in range(warmup, len(rows) - 1):
        r = rows[i]
        vals = (r.ma99, r.h99, r.adx14, r.atr14, r.rsi14, r.vol_ratio, r.res, r.sup)
        if any(pd.isna(v) for v in vals) or r.atr14 <= 0:
            continue
        prev, prev2 = rows[i - 1], rows[i - 2]
        signs = [1 if b.close > b.open else -1 if b.close < b.open else 0
                 for b in (prev2, prev, r)]
        streak3 = signs[0] == signs[1] == signs[2] != 0
        for pat, side in signals(r, prev, prev2):
            if busy.get(pat, -1) >= i:
                continue
            entry = r.close
            outcome, pnl, j_end = simulate(rows, i, side, entry,
                                           r.atr14 * atr_mult, rr, max_hold)
            busy[pat] = j_end
            room = (r.res - entry) if side == "LONG" else (entry - r.sup)
            results.append(TradeRes(
                ts=r.open_time, pattern=pat, side=side, outcome=outcome,
                pnl_pct=round(pnl, 4), adx=float(r.adx14), vol_ratio=float(r.vol_ratio),
                rsi=float(r.rsi14), hour=int(r.open_time.hour),
                room_atr=float(room / r.atr14),
                sep_pct=float(abs(r.ma7 - r.ma25) / entry * 100),
                weekday=int(r.open_time.weekday()),
                atr_pct=float(r.atr14 / r.close * 100),
                trend_age=int(r.up_age if side == "LONG" else r.dn_age),
                streak3=streak3))
    return results


# ---- kovalar ---------------------------------------------------------------
def adx_b(t): return "ADX<20" if t.adx < 20 else "ADX20-30" if t.adx < 30 else "ADX30+"
def vol_b(t): return "vol<1.0x" if t.vol_ratio < 1.0 else "vol1.0-1.5x" if t.vol_ratio < 1.5 else "vol1.5x+"
def rsi_b(t): return "RSI<35" if t.rsi < 35 else "RSI35-50" if t.rsi < 50 else "RSI50-65" if t.rsi < 65 else "RSI65+"
def hour_b(t): h = t.hour // 4 * 4; return f"{h:02d}-{h + 4:02d}utc"
def room_b(t): return "yer<2ATR" if t.room_atr < 2 else "yer2-4ATR" if t.room_atr < 4 else "yer4+ATR"
def side_b(t): return t.side
def wd_b(t): return WEEKDAYS[t.weekday]
def age_b(t): return "trend-genc(<10bar)" if t.trend_age < 10 else "trend-orta(10-30)" if t.trend_age <= 30 else "trend-yasli(30+)"
def strk_b(t): return "3mum-ayni-yon" if t.streak3 else "mumlar-karisik"


def make_atrp_b(trades):
    """Volatilite rejimini bu TF'in kendi dağılımına göre 3'e böler (tercile)."""
    vals = sorted(t.atr_pct for t in trades)
    if len(vals) < 3:
        return lambda t: "n/a"
    lo, hi = vals[len(vals) // 3], vals[2 * len(vals) // 3]

    def f(t):
        if t.atr_pct < lo:
            return f"volatilite-dusuk(<%{lo:.2f})"
        if t.atr_pct > hi:
            return f"volatilite-yuksek(>%{hi:.2f})"
        return "volatilite-orta"
    return f


def _stats(g):
    n = len(g)
    if n == 0:
        return 0, 0.0, 0.0
    wins = sum(1 for t in g if t.pnl_pct > 0)
    return n, wins / n * 100, sum(t.pnl_pct for t in g) / n


def bucket_lines(trades, keyfn, title) -> list[str]:
    groups: dict = {}
    for t in trades:
        groups.setdefault(keyfn(t), []).append(t)
    lines = [f"\n### {title}"]
    for k in sorted(groups):
        n, w, a = _stats(groups[k])
        tot = sum(t.pnl_pct for t in groups[k])
        flag = "" if n >= 30 else "  (n<30: fikir, kanıt değil)"
        lines.append(f"  {k:<22} n={n:<4} win%={w:5.1f}  ort%={a:+.3f}  toplam%={tot:+.1f}{flag}")
    return lines


def combo_lines(trades, min_n: int = 15) -> list[str]:
    groups: dict = {}
    for t in trades:
        groups.setdefault((t.pattern, t.side, adx_b(t), vol_b(t)), []).append(t)
    scored = [(k, *_stats(g)) for k, g in groups.items() if len(g) >= min_n]
    scored.sort(key=lambda x: x[3], reverse=True)
    lines = [f"\n### En iyi kombinasyonlar (pattern+yön+ADX+hacim, n>={min_n})"]
    for k, n, w, a in scored[:10]:
        lines.append(f"  {'+'.join(k):<46} n={n:<4} win%={w:5.1f}  ort%={a:+.3f}")
    lines.append("\n### En kötü kombinasyonlar (bunlardan KAÇIN)")
    for k, n, w, a in scored[-5:][::-1]:
        lines.append(f"  {'+'.join(k):<46} n={n:<4} win%={w:5.1f}  ort%={a:+.3f}")
    if not scored:
        lines.append("  (yeterli örnek yok)")
    return lines


def exit_matrix(d: pd.DataFrame, max_hold: int) -> list[str]:
    """Aynı sinyaller, farklı stop/hedef formülleri — hangi çıkış daha iyi?"""
    lines = ["\n### Çıkış formülü yarışı (tüm patternler havuz)"]
    for am, rr_ in EXIT_COMBOS:
        tr = walk_forward(d, max_hold=max_hold, atr_mult=am, rr=rr_)
        n, w, a = _stats(tr)
        tot = sum(t.pnl_pct for t in tr)
        lines.append(f"  stop={am:.1f}xATR hedef={rr_:.1f}R   n={n:<5} win%={w:5.1f}  "
                     f"ort%={a:+.3f}  toplam%={tot:+.1f}")
    return lines


def sample_good_trades(trades, k: int = 5) -> list[str]:
    good = sorted((t for t in trades if t.outcome == "HEDEF"),
                  key=lambda t: t.pnl_pct, reverse=True)[:k]
    lines = ["\n### Örnek SAĞLAM işlemler (hedefe gitmiş — koşullarına dikkat)"]
    for t in good:
        lines.append(f"  {t.ts:%Y-%m-%d %H:%M} {t.pattern} {t.side}  pnl%{t.pnl_pct:+.2f}  "
                     f"ADX{t.adx:.0f} vol{t.vol_ratio:.1f}x RSI{t.rsi:.0f} "
                     f"yer{t.room_atr:.1f}ATR trend{t.trend_age}bar")
    if not good:
        lines.append("  (hedefe giden işlem yok)")
    return lines


def insights(trades) -> list[str]:
    """Otomatik yorum: yalnızca iki tarafında da n>=20 olan karşılaştırmalarda konuşur."""
    out = ["\n### Otomatik yorum (öğrenilen kural adayları)"]
    if len(trades) < 30:
        out.append("  [!] Toplam işlem azdır; aşağıdakiler ön izlenimdir.")

    def cmp(a_list, b_list, a_name, b_name, konu):
        na, _, aa = _stats(a_list)
        nb, _, ab = _stats(b_list)
        if na >= 20 and nb >= 20:
            hukum = "İŞE YARIYOR" if aa > ab else "işe yaramıyor/ters"
            out.append(f"  - {konu}: {a_name} ort%={aa:+.3f} (n={na}) vs "
                       f"{b_name} ort%={ab:+.3f} (n={nb}) → {hukum}")
        else:
            out.append(f"  - {konu}: veri yetersiz (n={na}/{nb}, 20+ gerekir)")

    cmp([t for t in trades if t.adx >= 30], [t for t in trades if t.adx < 20],
        "ADX30+", "ADX<20", "ADX (trend gücü) filtresi")
    cmp([t for t in trades if t.vol_ratio >= 1.5], [t for t in trades if t.vol_ratio < 1.0],
        "hacim 1.5x+", "hacim <1.0x", "Hacim teyidi")
    cmp([t for t in trades if t.room_atr >= 2], [t for t in trades if t.room_atr < 2],
        "baraja 2+ ATR yer", "yer <2 ATR", "Direnç/desteğe mesafe")
    ext = [t for t in trades if (t.side == "LONG" and t.rsi >= 70) or (t.side == "SHORT" and t.rsi <= 30)]
    rest = [t for t in trades if not ((t.side == "LONG" and t.rsi >= 70) or (t.side == "SHORT" and t.rsi <= 30))]
    cmp(rest, ext, "normal RSI", "aşırı bölge (L:70+ / S:30-)", "RSI aşırılık koruması")
    cmp([t for t in trades if t.trend_age < 10], [t for t in trades if t.trend_age > 30],
        "genç trend (<10 bar)", "yaşlı trend (30+ bar)", "Trend yaşı (erken binmek)")
    cmp([t for t in trades if t.streak3], [t for t in trades if not t.streak3],
        "3 mum aynı yön", "mumlar karışık", "Momentum (ardışık mumlar)")
    cmp([t for t in trades if t.weekday >= 5], [t for t in trades if t.weekday < 5],
        "hafta sonu", "hafta içi", "Hafta sonu işlemi")
    cmp([t for t in trades if t.side == "SHORT"], [t for t in trades if t.side == "LONG"],
        "SHORT", "LONG", "Yön eğilimi (bu dönemde)")

    # en iyi saat bloğu
    blocks: dict = {}
    for t in trades:
        blocks.setdefault(hour_b(t), []).append(t)
    ok = [(k, *_stats(g)) for k, g in blocks.items() if len(g) >= 20]
    if ok:
        best = max(ok, key=lambda x: x[3])
        worst = min(ok, key=lambda x: x[3])
        out.append(f"  - Saat etkisi: en iyi {best[0]} (ort%={best[3]:+.3f}, n={best[1]}), "
                   f"en kötü {worst[0]} (ort%={worst[3]:+.3f}, n={worst[1]})")
    return out


def tf_overview(name: str, df: pd.DataFrame) -> str:
    rng = ((df["high"] - df["low"]) / df["close"] * 100)
    a = adx(df, 14).dropna()
    trending = float((a > 25).mean() * 100) if len(a) else 0.0
    return (f"  {name:<4} bar={len(df):<6} ort.mum-aralığı%={rng.mean():.3f}  "
            f"trendde geçen zaman(ADX>25)=%{trending:.0f}")


# ---- ana akış ---------------------------------------------------------------
def run_learning(symbol: str, days: int) -> str:
    print(f"Veri çekiliyor ({symbol}, {days} gün: 5m/15m/1h + 365 gün 1d)...")
    d5 = fetch_mainnet_klines(symbol, "5m", days)
    d15 = fetch_mainnet_klines(symbol, "15m", days)
    d1h = fetch_mainnet_klines(symbol, "1h", days)
    d1d = fetch_mainnet_klines(symbol, "1d", 365)
    print(f"  5m={len(d5)}  15m={len(d15)}  1h={len(d1h)}  1d={len(d1d)} bar")

    rep: list[str] = [
        f"# Öğrenilen Kurallar — {symbol} ({days} gün, walk-forward, 10 pattern)",
        f"Üretildi: {datetime.now():%Y-%m-%d %H:%M} | ana test: stop=1.5xATR hedef=2R | "
        f"komisyon %{FEE_RT_PCT} düşük | aynı-bar çakışması=STOP (kötümser)",
        "",
        "> DÜRÜSTLÜK NOTU: Bu rapor İLERİYİ GÖRMEDEN (walk-forward) üretildi ama",
        "> geçmişte çalışan gelecekte çalışmayabilir. n<30 kovalar FİKİRDİR.",
        "> P9_SEKME_MR bilerek trend filtresiz (mean-reversion işe yarıyor mu testi).",
        "",
        "## Piyasa bağlamı",
        tf_overview("5m", d5), tf_overview("15m", d15),
        tf_overview("1h", d1h), tf_overview("1d", d1d),
    ]

    plans = [  # (giriş TF, df, trend TF, trend df, max_hold, 24s S/R penceresi)
        ("5m", d5, "15m", d15, 96, 288),
        ("15m", d15, "1h", d1h, 96, 96),
        ("1h", d1h, "1d", d1d, 72, 24),
    ]
    for etf, edf, ttf, tdf, max_hold, sr_win in plans:
        print(f"{etf} girişleri test ediliyor (trend filtresi: {ttf})...")
        d = add_higher_trend(prep(edf, sr_win), prep(tdf, 24))
        trades = walk_forward(d, max_hold=max_hold)
        n, w, a = _stats(trades)
        rep.append(f"\n\n## {etf} girişleri (trend filtresi: {ttf}) — "
                   f"{n} işlem, win %{w:.1f}, ort %{a:+.3f}")
        # pattern tablosu
        pats: dict = {}
        for t in trades:
            pats.setdefault(t.pattern, []).append(t)
        rep.append("\n### Pattern performansı")
        order = sorted(pats, key=lambda k: _stats(pats[k])[2], reverse=True)
        for name in order:
            g = pats[name]
            pn, pw, pa = _stats(g)
            hs = sum(1 for t in g if t.outcome == "HEDEF")
            ss = sum(1 for t in g if t.outcome == "STOP")
            zs = sum(1 for t in g if t.outcome == "ZAMAN")
            flag = "" if pn >= 30 else "  (n<30)"
            rep.append(f"  {name:<13} n={pn:<4} win%={pw:5.1f}  ort%={pa:+.3f}  "
                       f"(hedef {hs}/stop {ss}/zaman {zs}){flag}")
        rep += bucket_lines(trades, side_b, "Yöne göre")
        rep += bucket_lines(trades, adx_b, "ADX'e göre")
        rep += bucket_lines(trades, vol_b, "Hacime göre")
        rep += bucket_lines(trades, rsi_b, "RSI'a göre")
        rep += bucket_lines(trades, room_b, "Direnç/desteğe mesafeye göre")
        rep += bucket_lines(trades, age_b, "Trend yaşına göre")
        rep += bucket_lines(trades, make_atrp_b(trades), "Volatilite rejimine göre (ATR%)")
        rep += bucket_lines(trades, strk_b, "Momentuma göre (son 3 mum)")
        rep += bucket_lines(trades, wd_b, "Haftanın gününe göre")
        rep += bucket_lines(trades, hour_b, "Saate göre (UTC)")
        rep += combo_lines(trades)
        rep += exit_matrix(d, max_hold)
        rep += sample_good_trades(trades)
        rep += insights(trades)

    rep.append("\n\n## Sonraki adım")
    rep.append("- Bu raporu copilot filtreleriyle karşılaştır; işe yaramayan filtre varsa gevşet/sık.")
    rep.append("- Raporu ara ara yeniden üret (piyasa rejimi değişir): 8-Ogrenme-Motoru.bat")
    return "\n".join(rep)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="Öğrenme Motoru (walk-forward)")
    ap.add_argument("--symbol", default="ETHUSDT")
    ap.add_argument("--days", type=int, default=45)
    args = ap.parse_args()

    report = run_learning(args.symbol, args.days)
    out = ROOT / "ogrenilen_kurallar.md"
    out.write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"\nRapor kaydedildi: {out}")


if __name__ == "__main__":
    main()
