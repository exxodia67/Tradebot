@echo off
cd /d "%~dp0"
echo Gercek (mainnet) gecmis veride strateji testi calisiyor (son 30 gun)...
echo.
.venv\Scripts\python.exe -m tradebot.backtest.runner --days 30
echo.
echo Cikmak icin bir tusa bas.
pause >nul
