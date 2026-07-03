# Tradebot — Binance Futures Testnet Daytrading Botu

Tak-çıkar stratejili, risk yönetimi merkezli, web dashboard'lu bir Binance
USDⓈ-M Futures trading botu. **Varsayılan olarak TESTNET ve dry-run (kağıt)
modunda** çalışır.

> ⚠️ **Uyarı:** Kaldıraçlı otomatik trading yüksek risklidir ve botların çoğu
> zamanla para kaybeder. Bu yazılım eğitim/araştırma amaçlıdır, **finansal tavsiye
> değildir**. Gerçek paraya geçmeden önce testnet'te uzun süre doğrulayın.

## Kurulum

```powershell
# 1) Sanal ortam
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2) Bağımlılıklar (editable install)
pip install -e .

# 3) Testnet anahtarları
copy .env.example .env
#   .env içine https://testnet.binancefuture.com adresinden alınan
#   API key/secret yaz. (Gerçek para YOK.)
```

## Çalıştırma

| Komut | Açıklama |
|------|----------|
| `python -m tradebot.config` | Yapılandırmayı doğrula |
| `python -m tradebot.exchange.binance_futures` | Bağlantı + bakiye + son mumlar |
| `python -m tradebot.backtest.runner --days 30` | **Gerçek (mainnet)** geçmiş veride strateji testi |
| `python -m tradebot.scanner --top 15` | Daytrading'e uygun coinleri tara (hacim + oynaklık) |
| `python -m tradebot.copilot --symbol ETHUSDT` | **Canlı Uyarı Co-pilot** — çift plan (15m/2R + 1h/3R), ikinci-giriş nöbeti, kaliteli kurulumda uyarır (yol gösterme) |
| `python -m tradebot.journal` | **Öğrenme raporu** — kapanan kurulumları yön/ADX/hacim/saat kovalarına göre analiz eder |
| `python -m tradebot.learner --days 45` | **Öğrenme motoru** — geçmişte walk-forward pattern testi → `ogrenilen_kurallar.md` |
| `python -m tradebot.telegram_bot` | **Telegram botu** — uyarılar cebine, /durum /analiz /journal komutları (7/24 için sunucuya kur) |
| `pytest` | Birim testleri (ağ gerektirmez) |
| `python -m tradebot.web.app` | Dashboard: http://127.0.0.1:8000 |
| `python -m tradebot.engine` | Dashboard'sız konsol modu |

## Ayarlar

- **`.env`** — API anahtarları, `USE_TESTNET`.
- **`config.yaml`** — sembol, timeframe, strateji parametreleri, **risk limitleri**,
  `dry_run`, sanal bakiye, dashboard host/port.

### Gerçek paraya geçiş (dikkatli!)
1. Testnet'te uzun süre tutarlı sonuç al.
2. `.env`'de `USE_TESTNET=false`, **canlı** API anahtarları gir.
3. `config.yaml`'da `engine.dry_run: false` yap.
4. Çok küçük sermaye + sıkı `risk.daily_max_loss_pct` ile başla.

## Mimari

```
config.yaml/.env → Config
   → BinanceFutures (ExchangeAdapter)
   → Strategy (ema_rsi)  → Signal
   → RiskManager (sizing, SL/TP, kaldıraç, kill-switch)
   → OrderExecutor (dry-run / testnet / canlı)
   → Store (SQLite)
   → Engine (async döngü) → Web dashboard (FastAPI + WebSocket)
                          → Backtester (aynı Strategy)
```

## Yeni strateji ekleme
`src/tradebot/strategy/` altında `Strategy`'den türet, `on_candle` doldur,
`strategy/__init__.py` REGISTRY'e ekle, `config.yaml`'da adını yaz.
