@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ================================================================
echo  OGRENME RAPORU  (journal'daki kapanmis kurulumlari analiz eder)
echo  Yon / ADX / Hacim / Saat kovalarina gore win-rate ve PnL doker.
echo  NOT: ~30+ islem olmadan sonuclar FIKIR verir, KANIT degildir.
echo ================================================================
.venv\Scripts\python.exe -m tradebot.journal
echo.
pause >nul
