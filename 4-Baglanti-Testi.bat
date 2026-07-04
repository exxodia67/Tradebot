@echo off
cd /d "%~dp0"
echo Binance testnet baglantisi ve hesap bakiyesi kontrol ediliyor...
echo (Secret key dogru girildiyse bakiye gorunur.)
echo.
.venv\Scripts\python.exe -m tradebot.exchange.binance_futures
echo.
echo Cikmak icin bir tusa bas.
pause >nul
