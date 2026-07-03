"""RiskManager testleri — sizing, kill-switch, cooldown, limitler."""
from tradebot.config import RiskConfig
from tradebot.models import Side
from tradebot.risk.manager import RiskManager


def make_rm(**over) -> RiskManager:
    base = dict(
        account_risk_pct=1.0, max_leverage=3,
        use_atr_stops=False, stop_loss_pct=1.0, tp_rr=2.0,
        max_open_positions=1, max_trades_per_day=10,
        cooldown_minutes=15, daily_max_loss_pct=5.0,
    )
    base.update(over)
    return RiskManager(RiskConfig(**base))


def test_position_sizing_is_risk_based():
    rm = make_rm()
    # bakiye 10.000, %1 risk = 100 USDT risk; stop %1 = fiyatın 1/100'ü
    d = rm.evaluate_entry("BTCUSDT", Side.LONG, price=100.0, balance=10_000.0)
    assert d.approved
    # stop mesafesi = 1.0 USDT -> qty = 100 / 1.0 = 100
    assert abs(d.quantity - 100.0) < 1e-6
    assert d.stop_loss == 100.0 - 1.0
    # TP = stop mesafesi (1.0) × tp_rr (2.0) = 2.0 -> 102
    assert d.take_profit == 102.0


def test_atr_stop_overrides_percent():
    rm = make_rm(use_atr_stops=True, atr_stop_mult=2.0, account_risk_pct=1.0)
    # ATR=5 -> stop mesafesi = 5*2 = 10; TP = 10*tp_rr(2) = 20
    d = rm.evaluate_entry("BTCUSDT", Side.LONG, price=100.0, balance=10_000.0, atr=5.0)
    assert d.approved
    assert d.stop_distance == 10.0
    assert d.stop_loss == 90.0
    assert d.take_profit == 120.0
    # risk 100 USDT / stop 10 = 10 adet
    assert abs(d.quantity - 10.0) < 1e-6


def test_daily_trade_cap_blocks_entries():
    rm = make_rm(max_trades_per_day=2, cooldown_minutes=0)
    for _ in range(2):
        assert rm.evaluate_entry("BTCUSDT", Side.LONG, 100.0, 10_000.0).approved
        rm.register_entry()
        rm.register_exit("BTCUSDT", 1.0)
    # günlük tavan (2) doldu -> üçüncü giriş reddedilir
    d = rm.evaluate_entry("BTCUSDT", Side.LONG, 100.0, 10_000.0)
    assert not d.approved
    assert "günlük işlem" in d.reason


def test_leverage_cap_limits_notional():
    # Çok geniş stop -> risk-bazlı qty küçük kalır; ama ters durumda notional sınırı devreye girer
    rm = make_rm(account_risk_pct=100.0, stop_loss_pct=0.1, max_leverage=3)
    d = rm.evaluate_entry("BTCUSDT", Side.LONG, price=100.0, balance=1_000.0)
    assert d.approved
    # notional kaldıraç tavanını aşamaz: qty*price <= balance*max_leverage
    assert d.quantity * 100.0 <= 1_000.0 * 3 + 1e-6


def test_short_stop_and_tp_directions():
    rm = make_rm()
    d = rm.evaluate_entry("BTCUSDT", Side.SHORT, price=100.0, balance=10_000.0)
    assert d.stop_loss > 100.0   # short'ta stop yukarıda
    assert d.take_profit < 100.0  # tp aşağıda


def test_max_open_positions_blocks_second_entry():
    rm = make_rm(max_open_positions=1)
    assert rm.evaluate_entry("BTCUSDT", Side.LONG, 100.0, 10_000.0).approved
    rm.register_entry()
    assert not rm.evaluate_entry("BTCUSDT", Side.LONG, 100.0, 10_000.0).approved


def test_cooldown_blocks_after_exit():
    rm = make_rm(cooldown_minutes=15)
    rm.register_entry()
    rm.register_exit("BTCUSDT", pnl=-5.0)
    d = rm.evaluate_entry("BTCUSDT", Side.LONG, 100.0, 10_000.0)
    assert not d.approved
    assert "cooldown" in d.reason


def test_daily_kill_switch_halts_trading():
    rm = make_rm(daily_max_loss_pct=5.0)
    rm.update_equity(10_000.0)         # gün başı equity
    rm.update_equity(9_400.0)          # -%6 -> kill-switch
    assert rm.halted
    d = rm.evaluate_entry("BTCUSDT", Side.LONG, 100.0, 9_400.0)
    assert not d.approved
    assert "durduruldu" in d.reason
