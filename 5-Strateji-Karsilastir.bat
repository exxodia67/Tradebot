@echo off
cd /d "%~dp0"
echo ================================================================
echo  Dort strateji ayni gercek veride karsilastiriliyor (komisyonlu)
echo  Donem: son 90 gun  ^|  Komisyon: %%0.04 taker
echo ================================================================
for %%S in (ema_rsi trend mean_reversion regime) do (
  echo.
  echo ---------------- STRATEJI: %%S ----------------
  .venv\Scripts\python.exe -m tradebot.backtest.runner --days 90 --strategy %%S 2>nul
)
echo.
echo Cikmak icin bir tusa bas.
pause >nul
