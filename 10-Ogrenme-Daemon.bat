@echo off
chcp 65001 >nul
title Tradebot - Surekli Ogrenme Daemon'u
cd /d "%~dp0"
echo Surekli ogrenme daemon'u baslatiliyor...
echo (10 dk'da bir Telegram raporu, 6 saatte bir tam ogrenme.)
echo (Obur PC'deki botla CAKISMAZ - bu sadece mesaj gonderir.)
:dongu
.venv\Scripts\python -m tradebot.ogrenme_daemon
echo Daemon durdu - 15 saniye sonra yeniden baslatiliyor...
timeout /t 15 >nul
goto dongu
