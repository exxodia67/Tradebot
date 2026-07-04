@echo off
chcp 65001 >nul
title Tradebot - Kur ve Calistir
cd /d "%~dp0"

REM ============================================================
REM  TEK DOSYA: baska bir PC'de bunu cift tikla, gerisi otomatik.
REM  Gereken tek sey: Python 3.11+ kurulu olmasi (python.org).
REM  Kurarken "Add python.exe to PATH" kutusunu ISARETLE.
REM ============================================================

where python >nul 2>nul
if errorlevel 1 (
  echo [HATA] Python bulunamadi.
  echo   1. https://www.python.org/downloads/ adresinden 3.11+ indir
  echo   2. Kurulumda "Add python.exe to PATH" kutusunu isaretle
  echo   3. Bu dosyayi tekrar calistir
  pause
  exit /b 1
)

if not exist .venv (
  echo [1/3] Sanal ortam kuruluyor...
  python -m venv .venv
)

echo [2/3] Bagimliliklar kuruluyor...
.venv\Scripts\python -m pip install -e . --quiet
if errorlevel 1 (
  echo [HATA] Kurulum basarisiz. Internet baglantisini kontrol et.
  pause
  exit /b 1
)

if exist .env goto calistir
echo.
echo Telegram bot token'i gerekli (Telegram'da @BotFather - /newbot).
set /p TOK=Token'i yapistir ve Enter'a bas:
>.env echo TELEGRAM_BOT_TOKEN=%TOK%
echo .env olusturuldu.

:calistir
echo [3/3] Bot baslatiliyor... Telegram'dan /start yaz.
echo (Durdurmak icin bu pencereyi kapat. Coker ise 10 sn'de kendini yeniden baslatir.)
:dongu
.venv\Scripts\python -m tradebot.telegram_bot
echo.
echo Bot durdu — 10 saniye sonra yeniden baslatiliyor...
timeout /t 10 >nul
goto dongu
