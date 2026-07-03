"""RiskManager — her giriş sinyalini emre çevirmeden önce denetler.

Çekirdek kurallar (config.risk'ten):
  * Risk-bazlı pozisyon boyutu (bakiyenin %'si / stop mesafesi)
  * Zorunlu stop-loss + opsiyonel take-profit fiyatları
  * Kaldıraç tavanı ve notional sınırı
  * Eşzamanlı pozisyon limiti + sembol cooldown
  * Günlük zarar kesici (kill-switch)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from loguru import logger

from tradebot.config import RiskConfig
from tradebot.models import Side


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    quantity: float = 0.0
    leverage: int = 1
    stop_loss: float | None = None
    take_profit: float | None = None
    stop_distance: float = 0.0   # bilgi/log amaçlı
    rr: float = 0.0              # ödül/risk oranı


class RiskManager:
    def __init__(self, risk: RiskConfig):
        self.r = risk
        self._open_positions = 0
        self._last_exit_at: dict[str, datetime] = {}
        self._day = self._today()
        self._day_start_equity: float | None = None
        self._realized_today = 0.0
        self._trades_today = 0
        self.halted = False  # kill-switch tetiklendi mi
        self.halt_reason = ""

    # ---- zaman kaynağı --------------------------------------------------
    # `now` enjekte edilebilir: canlıda gerçek saat, backtest'te mum zamanı.
    @staticmethod
    def _resolve_now(now: datetime | None) -> datetime:
        return now if now is not None else datetime.now(timezone.utc)

    # ---- gün takibi -----------------------------------------------------
    @staticmethod
    def _today(now: datetime | None = None) -> str:
        return RiskManager._resolve_now(now).strftime("%Y-%m-%d")

    def _roll_day_if_needed(self, equity: float, now: datetime | None = None) -> None:
        today = self._today(now)
        if today != self._day:
            self._day = today
            self._realized_today = 0.0
            self._trades_today = 0
            self._day_start_equity = equity
            self.halted = False
            self.halt_reason = ""
            logger.info("Yeni gün — günlük zarar/işlem sayaçları sıfırlandı.")

    # ---- kill-switch ----------------------------------------------------
    def update_equity(self, equity: float, now: datetime | None = None) -> None:
        """Her döngüde çağrılır; gün sınırını ve günlük zararı kontrol eder."""
        if self._day_start_equity is None:
            self._day_start_equity = equity
        self._roll_day_if_needed(equity, now)

        if self._day_start_equity and self._day_start_equity > 0:
            dd_pct = (equity - self._day_start_equity) / self._day_start_equity * 100
            if dd_pct <= -abs(self.r.daily_max_loss_pct) and not self.halted:
                self.halted = True
                self.halt_reason = (
                    f"Günlük zarar {dd_pct:.2f}% "
                    f"(limit -{self.r.daily_max_loss_pct}%) — KILL-SWITCH"
                )
                logger.error(self.halt_reason)

    def register_exit(self, symbol: str, pnl: float, now: datetime | None = None) -> None:
        self._open_positions = max(0, self._open_positions - 1)
        self._realized_today += pnl
        self._last_exit_at[symbol] = self._resolve_now(now)

    def register_entry(self) -> None:
        self._open_positions += 1
        self._trades_today += 1

    # ---- giriş değerlendirmesi ------------------------------------------
    def _in_cooldown(self, symbol: str, now: datetime | None = None) -> bool:
        last = self._last_exit_at.get(symbol)
        if not last:
            return False
        return self._resolve_now(now) - last < timedelta(
            minutes=self.r.cooldown_minutes
        )

    def evaluate_entry(
        self,
        symbol: str,
        side: Side,
        price: float,
        balance: float,
        atr: float | None = None,
        now: datetime | None = None,
    ) -> RiskDecision:
        """Giriş sinyalini denetle ve boyut/SL/TP hesapla.

        atr verilirse ve use_atr_stops açıksa stop mesafesi ATR'den (volatiliteye
        uyumlu) belirlenir; aksi halde stop_loss_pct yedeği kullanılır.
        now: zaman kaynağı (backtest'te mum zamanı; canlıda boş bırakılır).
        """
        if self.halted:
            return RiskDecision(False, f"durduruldu: {self.halt_reason}")
        if self._open_positions >= self.r.max_open_positions:
            return RiskDecision(False, "max açık pozisyon limitine ulaşıldı")
        if self._trades_today >= self.r.max_trades_per_day:
            return RiskDecision(False, "günlük işlem tavanına ulaşıldı")
        if self._in_cooldown(symbol, now):
            return RiskDecision(False, "cooldown süresi dolmadı")
        if balance <= 0:
            return RiskDecision(False, "bakiye yetersiz")

        # Stop mesafesi: ATR (tercih) veya yüzde (yedek)
        if self.r.use_atr_stops and atr and atr > 0:
            sl_dist = atr * self.r.atr_stop_mult
        else:
            sl_dist = price * self.r.stop_loss_pct / 100.0
        if sl_dist <= 0:
            return RiskDecision(False, "geçersiz stop mesafesi")

        # Take-profit, stop mesafesinin tp_rr katı (R bazlı asimetri)
        tp_dist = sl_dist * self.r.tp_rr if self.r.tp_rr > 0 else 0.0

        if side == Side.LONG:
            stop_loss = price - sl_dist
            take_profit = price + tp_dist if tp_dist > 0 else None
        else:
            stop_loss = price + sl_dist
            take_profit = price - tp_dist if tp_dist > 0 else None

        # Risk-bazlı miktar: bakiyenin %'si kadar risk / stop mesafesi
        risk_amount = balance * self.r.account_risk_pct / 100.0
        qty = risk_amount / sl_dist

        # Notional, kaldıraç tavanını aşmasın
        max_notional = balance * self.r.max_leverage
        if qty * price > max_notional:
            qty = max_notional / price
        if qty <= 0:
            return RiskDecision(False, "hesaplanan miktar sıfır")

        return RiskDecision(
            approved=True,
            reason="onaylandı",
            quantity=qty,
            leverage=self.r.max_leverage,
            stop_loss=stop_loss,
            take_profit=take_profit,
            stop_distance=sl_dist,
            rr=self.r.tp_rr,
        )
