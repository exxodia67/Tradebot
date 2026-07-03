"""Engine — feed → strategy → risk → executor → store ana döngüsü.

Async; dashboard tarafından başlatılıp durdurulabilir. Her kapanan mumda
stratejiyi çalıştırır, RiskManager onayıyla pozisyon açar/kapatır, sonuçları
SQLite'a yazar ve gözlemcilere (dashboard) durum yayını yapar.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable

from loguru import logger

import pandas as pd

from tradebot.config import Config
from tradebot.exchange.binance_futures import BinanceFutures
from tradebot.indicators import atr
from tradebot.execution.executor import OrderExecutor
from tradebot.models import SignalType
from tradebot.risk.manager import RiskManager
from tradebot.store.db import Store
from tradebot.strategy import build_strategy


@dataclass
class EngineState:
    running: bool = False
    symbol: str = ""
    timeframe: str = ""
    dry_run: bool = True
    testnet: bool = True
    last_price: float = 0.0
    equity: float = 0.0
    halted: bool = False
    halt_reason: str = ""
    position: dict | None = None
    last_signal: str = ""
    last_update: str = ""
    stats: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


Broadcast = Callable[[dict], Awaitable[None]]


async def _sleep_or_stop(event: asyncio.Event, timeout: float) -> None:
    """`timeout` saniye bekle; bu sırada stop event set edilirse erken dön."""
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        pass


class Engine:
    def __init__(self, config: Config, broadcast: Broadcast | None = None):
        self.cfg = config
        self.broadcast = broadcast
        self.exchange = BinanceFutures(config)
        self.strategy = build_strategy(config.strategy.name, config.strategy.params)
        self.risk = RiskManager(config.risk)
        self.executor = OrderExecutor(config, self.exchange)
        self.store = Store()
        self.state = EngineState(
            symbol=config.symbol, timeframe=config.timeframe,
            dry_run=config.engine.dry_run,
            testnet=config.secrets.use_testnet,
            equity=config.engine.paper_balance,
        )
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._last_candle_time = None

    # ---- bakiye ---------------------------------------------------------
    def _equity(self) -> float:
        if self.cfg.trading_enabled:
            try:
                return self.exchange.get_balance("USDT")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Bakiye okunamadı, sanal bakiye kullanılıyor: {e}")
        # dry-run: sanal bakiye + gerçekleşmiş günlük PnL
        return self.cfg.engine.paper_balance + self.risk._realized_today

    # ---- yaşam döngüsü --------------------------------------------------
    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self.state.running = True
        self._task = asyncio.create_task(self._run())
        logger.info("Engine başlatıldı.")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
        self.state.running = False
        logger.info("Engine durduruldu.")

    async def emergency_close(self) -> None:
        """Acil: açık pozisyonu kapat ve engine'i durdur."""
        if self.executor.position:
            trade = self.executor.close_position(self.state.last_price, "acil kapatma")
            if trade:
                self.risk.register_exit(trade.symbol, trade.pnl)
                self.store.save_trade(trade)
        await self.stop()

    # ---- ana döngü ------------------------------------------------------
    async def _run(self) -> None:
        sym, tf = self.cfg.symbol, self.cfg.timeframe
        poll = max(1, self.cfg.engine.poll_seconds)
        try:
            while not self._stop.is_set():
                try:
                    await self._tick(sym, tf)
                except Exception as e:  # noqa: BLE001 — döngü çökmesin
                    logger.exception(f"tick hatası: {e}")
                await _sleep_or_stop(self._stop, poll)
        finally:
            self.state.running = False

    async def _tick(self, symbol: str, timeframe: str) -> None:
        df = await asyncio.to_thread(
            self.exchange.get_klines, symbol, timeframe, 200
        )
        if df.empty:
            return

        last = df.iloc[-1]
        price = float(last["close"])
        self.state.last_price = price

        # Equity + kill-switch
        equity = await asyncio.to_thread(self._equity)
        self.state.equity = equity
        self.risk.update_equity(equity)
        self.state.halted = self.risk.halted
        self.state.halt_reason = self.risk.halt_reason

        # Dry-run: mum aralığında SL/TP tetiklendi mi?
        stop_trade = self.executor.check_stops(float(last["high"]), float(last["low"]))
        if stop_trade:
            self.risk.register_exit(stop_trade.symbol, stop_trade.pnl)
            self.store.save_trade(stop_trade)

        # Yeni kapanan mum mu? (aynı mumda tekrar işlem yapma)
        candle_time = last["open_time"]
        new_candle = candle_time != self._last_candle_time
        if new_candle:
            self._last_candle_time = candle_time
            await self._on_new_candle(df, symbol, price)

        await self._publish()

    def _latest_atr(self, df: pd.DataFrame) -> float | None:
        try:
            val = atr(df, self.risk.r.atr_period).iloc[-1]
            return float(val) if pd.notna(val) else None
        except Exception:  # noqa: BLE001
            return None

    async def _on_new_candle(self, df, symbol: str, price: float) -> None:
        signal = self.strategy.on_candle(df, self.executor.position)
        self.state.last_signal = f"{signal.type.value} ({signal.reason})"

        # Çıkış
        if signal.type == SignalType.EXIT and self.executor.position:
            trade = self.executor.close_position(price, signal.reason)
            if trade:
                self.risk.register_exit(trade.symbol, trade.pnl)
                self.store.save_trade(trade)
            return

        # Giriş
        if signal.is_entry and self.executor.position is None and not self.risk.halted:
            equity = self.state.equity
            atr_val = self._latest_atr(df)
            decision = self.risk.evaluate_entry(
                symbol, signal.side, price, equity, atr=atr_val
            )
            if decision.approved:
                self.executor.open_position(symbol, signal.side, price, decision)
                self.risk.register_entry()
                logger.info(
                    f"Pozisyon açıldı: {signal.side.value} {symbol} "
                    f"SL={decision.stop_loss:.2f} TP={decision.take_profit}"
                )
            else:
                logger.info(f"Giriş reddedildi: {decision.reason}")

    async def _publish(self) -> None:
        pos = self.executor.position
        self.state.position = (
            {
                "side": pos.side.value, "entry": pos.entry_price,
                "qty": pos.quantity, "sl": pos.stop_loss, "tp": pos.take_profit,
                "upnl": round(pos.unrealized_pnl(self.state.last_price), 4),
            }
            if pos else None
        )
        self.state.stats = self.store.stats()
        self.state.last_update = datetime.now(timezone.utc).isoformat()
        self.store.save_equity(self.state.equity)
        if self.broadcast:
            await self.broadcast(self.state.to_dict())


async def _main() -> None:
    """`python -m tradebot.engine` — dashboard'sız konsol modu (Ctrl+C ile dur)."""
    from tradebot.config import load_config

    cfg = load_config()
    eng = Engine(cfg)
    eng.start()
    logger.info("Konsol modunda çalışıyor. Durdurmak için Ctrl+C.")
    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        await eng.stop()


if __name__ == "__main__":
    asyncio.run(_main())
