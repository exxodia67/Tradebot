"""TradingView canlı analiz köprüsü.

TradingView'in tarama (scanner) uç noktasından 15m/1h/4h/1d için canlı
AL/SAT/NÖTR özetini çeker — TradingView'in kendi "Teknik Analiz" göstergesiyle
aynı kaynak (26 göstergenin oylaması: MA'lar + osilatörler).

Resmî bir API değildir; TradingView bozarsa /tv "okunamadı" der, bot etkilenmez.
Sadece OKUR — hesap, giriş, emir yok.

Skor -1..+1: -1 güçlü sat ... +1 güçlü al (TradingView eşikleri).
"""
from __future__ import annotations

import requests

SCAN_URL = "https://scanner.tradingview.com/crypto/scan"
# (etiket, kolon eki) — ek yoksa 1 günlük
TFS = (("15 dakika", "|15"), ("1 saat", "|60"), ("4 saat", "|240"), ("1 gün", ""))
TF_AD = {"15m": "15 dakika", "1h": "1 saat", "4h": "4 saat", "1d": "1 gün"}


def _label(v: float) -> str:
    if v >= 0.5:
        return "GÜÇLÜ AL 🟢"
    if v >= 0.1:
        return "AL 🟢"
    if v > -0.1:
        return "NÖTR ⚪"
    if v > -0.5:
        return "SAT 🔴"
    return "GÜÇLÜ SAT 🔴"


def _kisa(v: float) -> str:
    return _label(v).split(" 🟢")[0].split(" 🔴")[0].split(" ⚪")[0]


def ratings(symbol: str = "ETHUSDT") -> dict | None:
    """{tf_etiketi: {"toplam","ma","osilator","rsi"}, "close": fiyat} veya None."""
    cols: list[str] = []
    for _, suf in TFS:
        cols += [f"Recommend.All{suf}", f"Recommend.MA{suf}",
                 f"Recommend.Other{suf}", f"RSI{suf}"]
    cols.append("close")
    payload = {"symbols": {"tickers": [f"BINANCE:{symbol}.P"], "query": {"types": []}},
               "columns": cols}
    r = requests.post(SCAN_URL, json=payload, timeout=15)
    r.raise_for_status()
    data = r.json().get("data") or []
    if not data:
        return None
    d = data[0]["d"]
    out: dict = {"close": d[-1]}
    for i, (ad, _suf) in enumerate(TFS):
        t, ma, osc, rsi = d[i * 4: i * 4 + 4]
        if t is None:
            continue
        out[ad] = {"toplam": t, "ma": ma, "osilator": osc, "rsi": rsi}
    return out


def tv_text(symbol: str = "ETHUSDT") -> str:
    """/tv komutunun cevabı — düz Türkçe."""
    try:
        r = ratings(symbol)
    except Exception as e:  # noqa: BLE001
        return f"📺 TradingView okunamadı: {e}"
    if not r:
        return f"📺 TradingView'de BINANCE:{symbol}.P bulunamadı."
    lines = [f"📺 TradingView canlı analiz — {symbol} vadeli",
             f"Fiyat: {r['close']:.2f}$", ""]
    for ad, _suf in TFS:
        v = r.get(ad)
        if not v:
            continue
        lines.append(f"{ad}: {_label(v['toplam'])}  (skor {v['toplam']:+.2f})")
        lines.append(f"   ortalamalar {_kisa(v['ma'])} · osilatörler "
                     f"{_kisa(v['osilator'])} · RSI {v['rsi']:.0f}")
    lines.append("\nSkor -1..+1 (26 göstergenin oylaması). Botun kendi görüşü: /durum")
    lines.append(f"Grafik: https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}.P")
    return "\n".join(lines)


def uyum_satiri(symbol: str, tf: str, side: str) -> str | None:
    """Kurulum uyarısına eklenecek satır: TV aynı yönde mi düşünüyor?"""
    try:
        r = ratings(symbol)
    except Exception:  # noqa: BLE001
        return None
    v = (r or {}).get(TF_AD.get(tf, ""))
    if not v:
        return None
    lab = _kisa(v["toplam"])
    al_yon = side == "LONG"
    if (lab.endswith("AL") and al_yon) or (lab.endswith("SAT") and not al_yon):
        isaret = "uyum ✓"
    elif (lab.endswith("SAT") and al_yon) or (lab.endswith("AL") and not al_yon):
        isaret = "ÇELİŞKİ ⚠️ — dikkatli ol"
    else:
        isaret = "nötr"
    return f"📺 TradingView {TF_AD[tf]}: {lab} ({isaret})"


def tv_ozet_satiri(symbol: str = "ETHUSDT") -> str | None:
    """Saatlik rapora tek satır: '📺 TradingView: 15m AL · 1h NÖTR · ...'"""
    try:
        r = ratings(symbol)
    except Exception:  # noqa: BLE001
        return None
    if not r:
        return None
    kisa_ad = {"15 dakika": "15m", "1 saat": "1h", "4 saat": "4h", "1 gün": "1g"}
    parca = [f"{kisa_ad[ad]} {_kisa(v['toplam'])}"
             for ad, _ in TFS if (v := r.get(ad))]
    return "📺 TradingView: " + " · ".join(parca) if parca else None
