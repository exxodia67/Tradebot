# Tradebot — AI Trading Co-pilot (Telegram)

**TEK UYGULAMA**: `KUR-VE-CALISTIR.bat` çift tıkla → ana bot + tarayıcı + fırsat
avcısı aynı pencerede açılır, uyarılar Telegram'a düşer.

> ⚠️ **GERÇEK EMİR AÇMAZ.** Bot yalnızca analiz eder ve uyarır; işlemler kâğıt
> üzerinde takip edilir. Emir vermek istersen kararı ve emri SEN verirsin.
> Kaldıraçlı trading yüksek risklidir; bu yazılım finansal tavsiye değildir.

## Kurulum (boş bilgisayara)

`SIFIRDAN-KUR.bat` dosyasını indir, çift tıkla. Python'u kurar, botu GitHub'dan
indirir, bağımlılıkları kurar, Telegram token'ını sorar, başlatır.
Güncelleme de aynı dosyayla: tekrar çift tıkla (veriler/karneler korunur).

## Ne çalışıyor?

| Parça | Görev |
|------|-------|
| 🤖 Ana plan | ETH 15m + 1h — kurulum çıkınca ANINDA Telegram uyarısı, kâğıt işlem takibi |
| 🛰️ Tarayıcı | BTC + LINK — ana botla aynı kurallar, kanıt (karne) topluyor |
| 🎯 Fırsat Avcısı | 13 pattern'i canlı tarar; sadece 90 günde kanıtlanmış (ruhsatlı) olanlar konuşur |
| 🧠 Öğrenme | 6 saatte bir 90g walk-forward; saatte bir düz Türkçe rapor |
| 🌙 Gece nöbetçisi | PC kapalıyken GitHub Actions devralır (`.github/workflows/copilot.yml`) |

Hepsi tek proseste (`python -m tradebot.telegram_bot`), Telegram komutlarını
yalnızca ana bot dinler: `/durum` `/analiz` `/journal` `/ogren` `/tv` `/yardim`.
`/journal` üç karneyi (ana bot + tarayıcı + avcı) birden gösterir.

## Kurallar nasıl belirleniyor?

Her kural 90 günlük geriye dönük testle (komisyon %0.08 dahil, kötümser sayım)
aday olur; canlı karnede kanıt toplayarak yaşar ya da ölür. Ayrıntılar ve
tarihçe: `ogrenilen_kurallar.md`.

## Geliştirici notları

```powershell
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -e .
pytest                          # birim testleri (ağ gerektirmez)
python -m tradebot.learner      # öğrenme motorunu elle koştur
```

Kalıcı durum (karneler, telegram durumu) `%USERPROFILE%\.tradebot\` altında —
kurulum klasörü silinse de kaybolmaz.
