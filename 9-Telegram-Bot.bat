@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ================================================================
echo  TELEGRAM CO-PILOT BOTU  (gercek emir ACMAZ - yol gosterme)
echo  Kurulum (ilk sefer):
echo    1) Telegram'da @BotFather ac -> /newbot -> token al
echo    2) .env dosyasina ekle:  TELEGRAM_BOT_TOKEN=123456:ABC...
echo    3) Bu pencere aciliyken botuna Telegram'dan /start yaz
echo  Komutlar: /durum /analiz /journal /ogren /yardim
echo  Uyarilar (kurulum/stop/hedef) otomatik cebine gelir.
echo ================================================================
.venv\Scripts\python.exe -m tradebot.telegram_bot --symbol ETHUSDT
echo.
echo Bot durdu. Cikmak icin bir tusa bas.
pause >nul
