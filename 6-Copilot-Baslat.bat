@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ================================================================
echo  CANLI UYARI CO-PILOT v4  (yol gosterme modu - gercek emir ACMAZ)
echo  ETH/USDT cift plan izleniyor:
echo    [15m] giris + 1h trend  (hedef 2R)  - sik firsat
echo    [1h]  giris + 1d trend  (hedef 3R)  - seyrek ama en verimli
echo  Kaliteli kurulum cikinca UYARI verir; STOP sonrasi 4 saat
echo  "ikinci giris" nobeti tutar. Emri/STOP'u Binance'e SEN koyarsin.
echo  Log: copilot_log.txt  ^|  Durdurmak: Ctrl+C
echo ================================================================
.venv\Scripts\python.exe -m tradebot.copilot --symbol ETHUSDT --interval 30
echo.
echo Co-pilot durdu. Cikmak icin bir tusa bas.
pause >nul
