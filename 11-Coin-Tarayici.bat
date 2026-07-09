@echo off
chcp 65001 >nul
title Tradebot - Coin Tarayici (12 coin)
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo [HATA] Once SIFIRDAN-KUR.bat calistir - kurulum yok.
  pause
  exit /b 1
)
echo Coin tarayici baslatiliyor (BTC, BNB, SOL, XRP, ADA, DOGE, LTC, LINK, AVAX, DOT, TRX, NEAR)...
echo Ana botla ayni anda calisabilir - komut dinlemez, cakismaz.
:dongu
.venv\Scripts\python -m tradebot.tarayici
echo Tarayici durdu - 15 saniye sonra yeniden baslatiliyor...
timeout /t 15 >nul
goto dongu
