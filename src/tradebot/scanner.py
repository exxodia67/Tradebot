"""Coin tarayıcı — daytrading'e uygun adayları bulur (gerçek mainnet verisi).

Daytrading için iki şey gerekir: yeterli LİKİDİTE (kolay giriş/çıkış, dar spread)
ve yeterli OYNAKLIK (kâr fırsatı). Bu tarayıcı Binance Futures'taki tüm USDT
perpetual'larını çekip bu iki kritere göre sıralar. Sadece herkese açık veri;
anahtar gerektirmez.

Kullanım:
    python -m tradebot.scanner --top 15 --min-volume 50
"""
from __future__ import annotations

import argparse

from binance.client import Client


def scan(min_quote_volume_musd: float = 50.0) -> list[dict]:
    """24s ticker verisini çekip likidite filtresinden geçirip oynaklığa göre döndürür.

    min_quote_volume_musd: milyon USDT cinsinden minimum 24s hacim.
    """
    client = Client()  # mainnet public
    tickers = client.futures_ticker()
    rows = []
    for t in tickers:
        sym = t["symbol"]
        if not sym.endswith("USDT"):
            continue
        qv = float(t["quoteVolume"])  # 24s hacim (USDT)
        if qv < min_quote_volume_musd * 1_000_000:
            continue
        chg = float(t["priceChangePercent"])
        high, low = float(t["highPrice"]), float(t["lowPrice"])
        last = float(t["lastPrice"])
        # Gün-içi salınım aralığı (oynaklık göstergesi)
        range_pct = (high - low) / low * 100 if low > 0 else 0.0
        rows.append({
            "symbol": sym,
            "last": last,
            "chg_24h_pct": round(chg, 2),
            "range_24h_pct": round(range_pct, 2),
            "volume_musd": round(qv / 1_000_000, 0),
        })
    # Daytrading adayı: yüksek gün-içi salınım (likidite filtresinden geçenler arasında)
    rows.sort(key=lambda r: r["range_24h_pct"], reverse=True)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Daytrading coin tarayıcı")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--min-volume", type=float, default=50.0,
                    help="minimum 24s hacim (milyon USDT)")
    args = ap.parse_args()

    rows = scan(args.min_volume)
    print(f"\n=== DAYTRADING ADAYLARI (24s hacim > {args.min_volume:.0f}M USDT) ===")
    print(f"{'Sembol':<14}{'Fiyat':>12}{'24s %':>9}{'Gün-içi salınım %':>20}{'Hacim (M$)':>13}")
    print("-" * 68)
    for r in rows[: args.top]:
        print(f"{r['symbol']:<14}{r['last']:>12.4f}{r['chg_24h_pct']:>9}"
              f"{r['range_24h_pct']:>20}{r['volume_musd']:>13.0f}")
    print("\nNot: Yüksek oynaklık = yüksek fırsat AMA yüksek risk. Likidite filtresi "
          "uygulandı; yine de küçük coinlerde spread/slipaj yüksektir.")


if __name__ == "__main__":
    main()
