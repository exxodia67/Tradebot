@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ================================================================
echo  OGRENME MOTORU  (walk-forward: ileriyi gormeden gecmisi test eder)
echo  4 pattern x 5m/15m/1h girisleri, gercek mainnet verisiyle.
echo  Sonuc: ogrenilen_kurallar.md  (birkac dakika surebilir)
echo ================================================================
.venv\Scripts\python.exe -m tradebot.learner --symbol ETHUSDT --days 45
echo.
pause >nul
