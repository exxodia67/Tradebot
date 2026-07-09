"""Yapılandırma yükleme.

İki kaynak birleştirilir:
  * `.env`     -> API anahtarları ve testnet bayrağı (gizli)
  * `config.yaml` -> strateji / risk / engine / web parametreleri (gizli değil)

Tüm değerler pydantic ile doğrulanır; hatalı değerler erkenden yakalanır.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Proje kökü: .../Tradebot
ROOT = Path(__file__).resolve().parents[2]

# Kalıcı durum (journal, telegram durumu): kurulum klasöründen BAĞIMSIZ.
# Neden: bot farklı klasörlere kurulunca her kopya kendi journal'ını tutuyordu —
# işlemler "kayboluyordu" (06.07: gece stopları /journal'da görünmedi).
# GitHub Actions (gece nöbetçisi) TRADEBOT_STATE_DIR ile repo köküne yönlendirir
# — runner'ın ev klasörü her koşuda sıfırlanır, repo'daki dosyalar commit'lenir.
import os

_env_state = os.environ.get("TRADEBOT_STATE_DIR")
STATE_DIR = Path(_env_state) if _env_state else Path.home() / ".tradebot"
STATE_DIR.mkdir(parents=True, exist_ok=True)


class Secrets(BaseSettings):
    """`.env` dosyasından okunan gizli değerler."""

    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    binance_api_key: str = Field(default="", alias="BINANCE_API_KEY")
    binance_api_secret: str = Field(default="", alias="BINANCE_API_SECRET")
    use_testnet: bool = Field(default=True, alias="USE_TESTNET")

    # Telegram bot (uyarılar + komutlar). @BotFather'dan token al, .env'e yaz.
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")


class StrategyConfig(BaseModel):
    name: str = "ema_rsi"
    params: dict = Field(default_factory=dict)


class RiskConfig(BaseModel):
    account_risk_pct: float = 1.0
    max_leverage: int = 3

    # Dinamik (ATR) stop — sabit %'ye göre volatiliteye uyum sağlar
    use_atr_stops: bool = True
    atr_period: int = 14
    atr_stop_mult: float = 1.5      # stop mesafesi = ATR × bu çarpan
    stop_loss_pct: float = 1.0      # ATR yoksa/kapalıysa yedek stop mesafesi (%)

    # Take-profit, stop mesafesinin katı olarak (R bazlı asimetri)
    tp_rr: float = 2.0              # TP = stop_mesafesi × tp_rr (0 = TP kapalı)

    max_open_positions: int = 1
    max_trades_per_day: int = 10    # komisyon koruması: günlük işlem tavanı
    cooldown_minutes: int = 15
    daily_max_loss_pct: float = 5.0

    @field_validator("stop_loss_pct")
    @classmethod
    def stop_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("stop_loss_pct > 0 olmalı (zorunlu stop-loss).")
        return v


class EngineConfig(BaseModel):
    poll_seconds: int = 5
    dry_run: bool = True
    paper_balance: float = 10000.0  # dry-run başlangıç sanal bakiyesi (USDT)


class WebConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000


class Config(BaseModel):
    """Birleşik yapılandırma."""

    symbol: str = "BTCUSDT"
    timeframe: str = "5m"
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    engine: EngineConfig = Field(default_factory=EngineConfig)
    web: WebConfig = Field(default_factory=WebConfig)

    # Çalışma anında doldurulur (.env'den)
    secrets: Secrets = Field(default_factory=Secrets)

    @property
    def trading_enabled(self) -> bool:
        """Gerçek emir gönderilebilir mi? dry_run kapalı VE anahtarlar var ise."""
        return (
            not self.engine.dry_run
            and bool(self.secrets.binance_api_key)
            and bool(self.secrets.binance_api_secret)
        )


def load_config(path: Path | str | None = None) -> Config:
    """`config.yaml` + `.env` okuyup doğrulanmış Config döndürür."""
    load_dotenv(ROOT / ".env")
    cfg_path = Path(path) if path else ROOT / "config.yaml"

    data: dict = {}
    if cfg_path.exists():
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    cfg = Config(**data)
    cfg.secrets = Secrets()  # .env'den taze oku
    return cfg


if __name__ == "__main__":
    c = load_config()
    print("Yüklenen yapılandırma:")
    print(f"  symbol={c.symbol} timeframe={c.timeframe}")
    print(f"  strateji={c.strategy.name} params={c.strategy.params}")
    print(f"  testnet={c.secrets.use_testnet} dry_run={c.engine.dry_run}")
    print(f"  trading_enabled={c.trading_enabled}")
