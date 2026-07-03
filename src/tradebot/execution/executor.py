"""OrderExecutor — RiskDecision'ı gerçek/sahte emirlere çevirir.

dry_run=True iken hiçbir gerçek emir gönderilmez; pozisyon yalnız bellekte
simüle edilir (kağıt mod). Bu, testnet anahtarı olmadan bile engine'in
uçtan uca çalışmasını sağlar.
"""
from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger

from tradebot.config import Config
from tradebot.exchange.base import ExchangeAdapter
from tradebot.models import Position, Side, Trade
from tradebot.risk.manager import RiskDecision


class OrderExecutor:
    def __init__(self, config: Config, exchange: ExchangeAdapter):
        self.cfg = config
        self.ex = exchange
        self.dry_run = config.engine.dry_run
        self.position: Position | None = None

    # ---- giriş ----------------------------------------------------------
    def open_position(
        self, symbol: str, side: Side, price: float, decision: RiskDecision
    ) -> Position:
        qty = decision.quantity
        if not self.dry_run:
            self.ex.set_leverage(symbol, decision.leverage)
            qty = self.ex.quantize_qty(symbol, qty)  # type: ignore[attr-defined]
            self.ex.market_order(symbol, side, qty)
            self.ex.place_stop_orders(
                symbol, side, qty, decision.stop_loss, decision.take_profit
            )
        else:
            logger.info(f"[DRY-RUN] {side.value} aç {symbol} qty={qty:.6f} @ {price}")

        self.position = Position(
            symbol=symbol, side=side, entry_price=price, quantity=qty,
            leverage=decision.leverage,
            stop_loss=decision.stop_loss, take_profit=decision.take_profit,
        )
        return self.position

    # ---- çıkış ----------------------------------------------------------
    def close_position(self, price: float, reason: str = "") -> Trade | None:
        pos = self.position
        if pos is None:
            return None

        close_side = Side.SHORT if pos.side == Side.LONG else Side.LONG
        if not self.dry_run:
            self.ex.cancel_all(pos.symbol)
            self.ex.market_order(pos.symbol, close_side, pos.quantity)
        else:
            logger.info(f"[DRY-RUN] {pos.side.value} kapat {pos.symbol} @ {price} ({reason})")

        pnl = pos.unrealized_pnl(price)
        trade = Trade(
            symbol=pos.symbol, side=pos.side, entry_price=pos.entry_price,
            exit_price=price, quantity=pos.quantity, pnl=pnl, reason=reason,
            opened_at=pos.opened_at, closed_at=datetime.now(timezone.utc),
        )
        logger.info(f"İşlem kapandı: {pos.symbol} {pos.side.value} PnL={pnl:.4f} USDT")
        self.position = None
        return trade

    # ---- SL/TP simülasyonu (dry-run) ------------------------------------
    def check_stops(self, high: float, low: float) -> Trade | None:
        """Dry-run modda SL/TP'ye değildi mi diye mum aralığını kontrol et."""
        pos = self.position
        if pos is None or not self.dry_run:
            return None
        if pos.side == Side.LONG:
            if pos.stop_loss and low <= pos.stop_loss:
                return self.close_position(pos.stop_loss, "stop-loss")
            if pos.take_profit and high >= pos.take_profit:
                return self.close_position(pos.take_profit, "take-profit")
        else:
            if pos.stop_loss and high >= pos.stop_loss:
                return self.close_position(pos.stop_loss, "stop-loss")
            if pos.take_profit and low <= pos.take_profit:
                return self.close_position(pos.take_profit, "take-profit")
        return None
