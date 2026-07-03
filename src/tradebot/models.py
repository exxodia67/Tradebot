"""Katmanlar arası paylaşılan veri tipleri."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"  # pozisyon yok / kapat


class SignalType(str, Enum):
    ENTER_LONG = "ENTER_LONG"
    ENTER_SHORT = "ENTER_SHORT"
    EXIT = "EXIT"
    HOLD = "HOLD"


@dataclass
class Signal:
    """Stratejinin ürettiği karar."""

    type: SignalType
    reason: str = ""
    price: float | None = None  # sinyal anındaki referans fiyat

    @property
    def is_entry(self) -> bool:
        return self.type in (SignalType.ENTER_LONG, SignalType.ENTER_SHORT)

    @property
    def side(self) -> Side:
        if self.type == SignalType.ENTER_LONG:
            return Side.LONG
        if self.type == SignalType.ENTER_SHORT:
            return Side.SHORT
        return Side.FLAT


@dataclass
class Position:
    symbol: str
    side: Side
    entry_price: float
    quantity: float
    leverage: int
    stop_loss: float | None = None
    take_profit: float | None = None
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def unrealized_pnl(self, mark_price: float) -> float:
        """İşaret fiyatına göre gerçekleşmemiş K/Z (USDT)."""
        if self.side == Side.LONG:
            return (mark_price - self.entry_price) * self.quantity
        if self.side == Side.SHORT:
            return (self.entry_price - mark_price) * self.quantity
        return 0.0


@dataclass
class Trade:
    """Kapanmış işlem kaydı (DB'ye yazılır)."""

    symbol: str
    side: Side
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    reason: str = ""
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
