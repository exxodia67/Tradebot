"""Basit bar-bazlı backtester.

Aynı Strategy ve RiskManager kodunu kullanır; böylece canlıda gördüğün
davranışın geçmiş veri üzerindeki karşılığını ölçersin. Mum-içi sıralamada
önce stop/TP (mum high/low'una göre), sonra strateji çıkış/giriş kararı gelir.

Veri GERÇEK (mainnet) geçmiş fiyatlardan gelir — testnet'in yapay verisi
backtest için yanıltıcıdır. Sadece herkese açık fiyat verisi okunur; gerçek
emir/para gerektirmez.

Kullanım:
    python -m tradebot.backtest.runner --symbol BTCUSDT --timeframe 5m --days 30
"""
from __future__ import annotations

import argparse

import pandas as pd
from loguru import logger

from tradebot.config import load_config
from tradebot.exchange.binance_futures import fetch_mainnet_klines
from tradebot.indicators import atr
from tradebot.models import Position, Side, SignalType
from tradebot.risk.manager import RiskManager
from tradebot.strategy import build_strategy


def run_backtest(df: pd.DataFrame, cfg, warmup: int = 50, fee_rate: float = 0.0004) -> dict:
    """fee_rate: işlem başına TEK YÖN komisyon (Binance taker ~0.0004 = %0.04).
    Gidiş-dönüş = giriş + çıkış olarak uygulanır."""
    strategy = build_strategy(cfg.strategy.name, cfg.strategy.params)
    risk = RiskManager(cfg.risk)
    atr_series = atr(df, cfg.risk.atr_period)

    equity = cfg.engine.paper_balance
    start_equity = equity
    peak = equity
    max_dd = 0.0
    total_fees = 0.0
    pos: Position | None = None
    trades: list[dict] = []
    sizes: list[dict] = []  # her girişin notional + efektif kaldıracı

    def close(price: float, reason: str, now) -> None:
        nonlocal pos, equity, total_fees
        if pos is None:
            return
        gross = pos.unrealized_pnl(price)
        fee = fee_rate * pos.quantity * (pos.entry_price + price)  # giriş + çıkış
        pnl = gross - fee
        equity += pnl
        total_fees += fee
        risk.register_exit(pos.symbol, pnl, now=now)
        trades.append({"side": pos.side.value, "entry": pos.entry_price,
                       "exit": price, "pnl": pnl, "reason": reason})
        pos = None

    for i in range(warmup, len(df)):
        window = df.iloc[: i + 1]
        bar = window.iloc[-1]
        high, low, price = float(bar["high"]), float(bar["low"]), float(bar["close"])
        now = bar["open_time"].to_pydatetime()  # simüle zaman = mum zamanı

        # Gün sınırı + kill-switch (canlıdaki gibi)
        risk.update_equity(equity, now=now)

        # 1) Mum-içi stop/TP
        if pos is not None:
            if pos.side == Side.LONG:
                if pos.stop_loss and low <= pos.stop_loss:
                    close(pos.stop_loss, "stop-loss", now)
                elif pos.take_profit and high >= pos.take_profit:
                    close(pos.take_profit, "take-profit", now)
            else:
                if pos.stop_loss and high >= pos.stop_loss:
                    close(pos.stop_loss, "stop-loss", now)
                elif pos.take_profit and low <= pos.take_profit:
                    close(pos.take_profit, "take-profit", now)

        # 2) Strateji kararı
        sig = strategy.on_candle(window, pos)
        if sig.type == SignalType.EXIT and pos is not None:
            close(price, sig.reason, now)
        elif sig.is_entry and pos is None and not risk.halted:
            atr_val = atr_series.iloc[i]
            atr_val = float(atr_val) if pd.notna(atr_val) else None
            dec = risk.evaluate_entry(
                cfg.symbol, sig.side, price, equity, atr=atr_val, now=now
            )
            if dec.approved:
                pos = Position(
                    symbol=cfg.symbol, side=sig.side, entry_price=price,
                    quantity=dec.quantity, leverage=dec.leverage,
                    stop_loss=dec.stop_loss, take_profit=dec.take_profit,
                )
                notional = dec.quantity * price
                sizes.append({"notional": notional, "lev": notional / equity})
                risk.register_entry()

        # Drawdown takibi
        cur = equity + (pos.unrealized_pnl(price) if pos else 0.0)
        peak = max(peak, cur)
        max_dd = max(max_dd, (peak - cur) / peak * 100 if peak > 0 else 0.0)

    # Açık pozisyonu son fiyattan kapat
    if pos is not None:
        close(float(df.iloc[-1]["close"]), "backtest sonu",
              df.iloc[-1]["open_time"].to_pydatetime())

    n = len(trades)
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    return {
        "candles": len(df),
        "trades": n,
        "win_rate": round(len(wins) / n * 100, 1) if n else 0.0,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "payoff": round(avg_win / abs(avg_loss), 2) if avg_loss else 0.0,
        "avg_notional": round(sum(s["notional"] for s in sizes) / len(sizes), 0) if sizes else 0,
        "avg_leverage": round(sum(s["lev"] for s in sizes) / len(sizes), 2) if sizes else 0,
        "net_pnl": round(equity - start_equity, 2),
        "return_pct": round((equity - start_equity) / start_equity * 100, 2),
        "total_fees": round(total_fees, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "end_equity": round(equity, 2),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Tradebot backtester (mainnet verisi)")
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--timeframe", default=None)
    ap.add_argument("--days", type=int, default=30, help="kaç günlük geçmiş veri")
    ap.add_argument("--strategy", default=None,
                    help="config'i geçersiz kıl (ema_rsi/trend/mean_reversion/regime)")
    ap.add_argument("--fee", type=float, default=0.0004,
                    help="tek yön komisyon oranı (0.0004 = %%0.04 taker; 0 = komisyonsuz)")
    ap.add_argument("--max-lev", type=int, default=None, help="kaldıraç tavanını geçersiz kıl")
    ap.add_argument("--risk-pct", type=float, default=None, help="işlem başı risk %%'sini geçersiz kıl")
    args = ap.parse_args()

    cfg = load_config()
    symbol = args.symbol or cfg.symbol
    tf = args.timeframe or cfg.timeframe
    cfg.symbol = symbol
    cfg.timeframe = tf
    if args.strategy:
        cfg.strategy.name = args.strategy
        cfg.strategy.params = {}  # varsayılan parametreleri kullan
    if args.max_lev is not None:
        cfg.risk.max_leverage = args.max_lev
    if args.risk_pct is not None:
        cfg.risk.account_risk_pct = args.risk_pct

    logger.info(f"Mainnet'ten {args.days} günlük {symbol} {tf} verisi çekiliyor...")
    df = fetch_mainnet_klines(symbol, tf, args.days)
    logger.info(f"{len(df)} mum çekildi. Backtest çalışıyor...")

    result = run_backtest(df, cfg, fee_rate=args.fee)
    first = df.iloc[0]["open_time"].date()
    last = df.iloc[-1]["open_time"].date()
    print("\n=== BACKTEST SONUCU (GERCEK veri) ===")
    print(f"  strateji         : {cfg.strategy.name}")
    print(f"  donem            : {first} - {last}")
    for k, v in result.items():
        print(f"  {k:18}: {v}")
    print("\nNot: Komisyon/slipaj dahil değildir; sonuçlar idealize edilmiştir.")


if __name__ == "__main__":
    main()
