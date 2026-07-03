"""SQLite tabanlı işlem/equity kaydı (SQLAlchemy 2.0)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import DateTime, Float, Integer, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from tradebot.config import ROOT
from tradebot.models import Trade


class Base(DeclarativeBase):
    pass


class TradeRow(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20))
    side: Mapped[str] = mapped_column(String(8))
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[float] = mapped_column(Float)
    pnl: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(64), default="")
    opened_at: Mapped[datetime] = mapped_column(DateTime)
    closed_at: Mapped[datetime] = mapped_column(DateTime)


class EquityRow(Base):
    __tablename__ = "equity"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime)
    equity: Mapped[float] = mapped_column(Float)


class Store:
    def __init__(self, db_path: Path | str | None = None):
        path = Path(db_path) if db_path else ROOT / "tradebot.db"
        self.engine = create_engine(f"sqlite:///{path}", future=True)
        Base.metadata.create_all(self.engine)

    def save_trade(self, t: Trade) -> None:
        with Session(self.engine) as s:
            s.add(TradeRow(
                symbol=t.symbol, side=t.side.value, entry_price=t.entry_price,
                exit_price=t.exit_price, quantity=t.quantity, pnl=t.pnl,
                reason=t.reason, opened_at=t.opened_at, closed_at=t.closed_at,
            ))
            s.commit()

    def save_equity(self, equity: float) -> None:
        with Session(self.engine) as s:
            s.add(EquityRow(ts=datetime.now(timezone.utc), equity=equity))
            s.commit()

    def recent_trades(self, limit: int = 50) -> list[dict]:
        with Session(self.engine) as s:
            rows = s.scalars(
                select(TradeRow).order_by(TradeRow.id.desc()).limit(limit)
            ).all()
            return [
                {
                    "id": r.id, "symbol": r.symbol, "side": r.side,
                    "entry_price": r.entry_price, "exit_price": r.exit_price,
                    "quantity": r.quantity, "pnl": r.pnl, "reason": r.reason,
                    "closed_at": r.closed_at.isoformat(),
                }
                for r in rows
            ]

    def stats(self) -> dict:
        with Session(self.engine) as s:
            rows = s.scalars(select(TradeRow)).all()
        n = len(rows)
        if n == 0:
            return {"trades": 0, "total_pnl": 0.0, "win_rate": 0.0}
        wins = sum(1 for r in rows if r.pnl > 0)
        return {
            "trades": n,
            "total_pnl": round(sum(r.pnl for r in rows), 4),
            "win_rate": round(wins / n * 100, 1),
        }
