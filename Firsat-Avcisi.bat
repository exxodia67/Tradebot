@echo off
chcp 65001 >nul
title Tradebot - Firsat Avcisi (13 pattern)
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo [HATA] Once SIFIRDAN-KUR.bat calistir - kurulum yok.
  pause
  exit /b 1
)
echo Firsat avcisi baslatiliyor (ETH, BTC, LINK - 13 pattern, ruhsatlilar konusur)...
echo Ana botla ayni anda calisabilir - komut dinlemez, cakismaz.
:dongu
.venv\Scripts\python -m tradebot.avci
echo Avci durdu - 15 saniye sonra yeniden baslatiliyor...
timeout /t 15 >nul
goto dongu
