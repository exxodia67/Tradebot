"""Binance USDⓈ-M Futures adaptörü (python-binance üzerinde).

Varsayılan olarak TESTNET'e bağlanır. Gerçek emir yalnızca dry_run kapalı
ve geçerli anahtarlar varken `OrderExecutor` tarafından çağrılır.
"""
from __future__ import annotations

import math

import pandas as pd
from binance.client import Client
from loguru import logger

from tradebot.config import Config
from tradebot.exchange.base import ExchangeAdapter
from tradebot.models import Side

_KLINE_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "qav", "trades", "tbav", "tqav", "ignore",
]


def klines_to_df(raw: list) -> pd.DataFrame:
    """Binance ham kline listesini standart DataFrame'e çevirir."""
    df = pd.DataFrame(raw, columns=_KLINE_COLS)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    return df[["open_time", "open", "high", "low", "close", "volume", "close_time"]]


def fetch_mainnet_klines(symbol: str, interval: str, days: int) -> pd.DataFrame:
    """Backtest için GERÇEK (mainnet) geçmiş mumlar — anahtar gerektirmez.

    python-binance otomatik sayfalama yapar; testnet'in yapay verisi yerine
    gerçek piyasa hareketini verir. (Sadece herkese açık fiyat verisi.)
    Ağ dalgalanmasına karşı 30s timeout + 3 deneme.
    """
    import time as _time

    client = Client(requests_params={"timeout": 30})  # mainnet public
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            raw = client.futures_historical_klines(symbol, interval, f"{days} day ago UTC")
            return klines_to_df(raw)
        except Exception as e:  # noqa: BLE001 — ağ hatasında bekle ve tekrar dene
            last_err = e
            logger.warning(f"kline çekme hatası (deneme {attempt + 1}/3): {e}")
            _time.sleep(3 * (attempt + 1))
    raise last_err


class BinanceFutures(ExchangeAdapter):
    def __init__(self, config: Config):
        self.cfg = config
        s = config.secrets
        self.client = Client(
            api_key=s.binance_api_key or None,
            api_secret=s.binance_api_secret or None,
            testnet=s.use_testnet,
        )
        if s.use_testnet:
            logger.info("Binance Futures TESTNET'e bağlanıldı.")
        else:
            logger.warning("Binance Futures CANLI (gerçek para) moduna bağlanıldı!")
        self._filters: dict[str, dict] = {}

    # ---- piyasa filtreleri (miktar/fiyat hassasiyeti) --------------------
    def _symbol_filters(self, symbol: str) -> dict:
        if symbol not in self._filters:
            info = self.client.futures_exchange_info()
            for s in info["symbols"]:
                if s["symbol"] == symbol:
                    f = {flt["filterType"]: flt for flt in s["filters"]}
                    self._filters[symbol] = {
                        "step": float(f["LOT_SIZE"]["stepSize"]),
                        "tick": float(f["PRICE_FILTER"]["tickSize"]),
                        "min_qty": float(f["LOT_SIZE"]["minQty"]),
                    }
                    break
        return self._filters.get(symbol, {"step": 0.001, "tick": 0.1, "min_qty": 0.001})

    def quantize_qty(self, symbol: str, qty: float) -> float:
        step = self._symbol_filters(symbol)["step"]
        return math.floor(qty / step) * step

    def quantize_price(self, symbol: str, price: float) -> float:
        tick = self._symbol_filters(symbol)["tick"]
        return math.floor(price / tick) * tick

    def min_qty(self, symbol: str) -> float:
        return self._symbol_filters(symbol)["min_qty"]

    # ---- ExchangeAdapter ------------------------------------------------
    def ping(self) -> bool:
        self.client.futures_ping()
        return True

    def get_balance(self, asset: str = "USDT") -> float:
        for b in self.client.futures_account_balance():
            if b["asset"] == asset:
                return float(b["availableBalance"])
        return 0.0

    def get_klines(self, symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
        raw = self.client.futures_klines(symbol=symbol, interval=interval, limit=limit)
        return klines_to_df(raw)

    def get_mark_price(self, symbol: str) -> float:
        return float(self.client.futures_mark_price(symbol=symbol)["markPrice"])

    def set_leverage(self, symbol: str, leverage: int) -> None:
        self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
        logger.info(f"{symbol} kaldıraç = {leverage}x")

    def market_order(self, symbol: str, side: Side, quantity: float) -> dict:
        binance_side = "BUY" if side == Side.LONG else "SELL"
        qty = self.quantize_qty(symbol, quantity)
        logger.info(f"MARKET {binance_side} {symbol} qty={qty}")
        return self.client.futures_create_order(
            symbol=symbol, side=binance_side, type="MARKET", quantity=qty
        )

    def place_stop_orders(
        self,
        symbol: str,
        side: Side,
        quantity: float,
        stop_loss: float | None,
        take_profit: float | None,
    ) -> dict:
        # Pozisyonu kapatacak yön: long ise SELL, short ise BUY
        close_side = "SELL" if side == Side.LONG else "BUY"
        result: dict = {}
        if stop_loss:
            sp = self.quantize_price(symbol, stop_loss)
            result["sl"] = self.client.futures_create_order(
                symbol=symbol, side=close_side, type="STOP_MARKET",
                stopPrice=sp, closePosition=True,
            )
            logger.info(f"SL emri @ {sp}")
        if take_profit:
            tp = self.quantize_price(symbol, take_profit)
            result["tp"] = self.client.futures_create_order(
                symbol=symbol, side=close_side, type="TAKE_PROFIT_MARKET",
                stopPrice=tp, closePosition=True,
            )
            logger.info(f"TP emri @ {tp}")
        return result

    def cancel_all(self, symbol: str) -> None:
        try:
            self.client.futures_cancel_all_open_orders(symbol=symbol)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"cancel_all hata: {e}")

    def get_position(self, symbol: str) -> dict | None:
        for p in self.client.futures_position_information(symbol=symbol):
            amt = float(p["positionAmt"])
            if amt != 0.0:
                return {
                    "symbol": symbol,
                    "amount": amt,
                    "side": Side.LONG if amt > 0 else Side.SHORT,
                    "entry_price": float(p["entryPrice"]),
                    "unrealized": float(p["unRealizedProfit"]),
                }
        return None


def _check() -> None:
    """`python -m tradebot.exchange.binance_futures` — bağlantı testi."""
    from tradebot.config import load_config

    cfg = load_config()
    ex = BinanceFutures(cfg)
    ex.ping()
    print("Ping OK.")
    try:
        print(f"USDT bakiye (testnet): {ex.get_balance():.2f}")
    except Exception as e:  # noqa: BLE001
        print(f"Bakiye okunamadı (anahtar gerekli): {e}")
    df = ex.get_klines(cfg.symbol, cfg.timeframe, limit=5)
    print(f"Son {len(df)} mum:")
    print(df.to_string(index=False))


if __name__ == "__main__":
    _check()
