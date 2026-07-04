@echo off
chcp 65001 >nul
title Tradebot - SIFIRDAN KURULUM
setlocal

REM =====================================================================
REM  BU TEK DOSYA HER SEYI YAPAR. Bos bilgisayara at, cift tikla, bekle.
REM  1) Python yoksa indirir ve kurar
REM  2) Botu GitHub'dan indirir
REM  3) Kurar, token sorar, baslatir
REM =====================================================================

set "KURDIZIN=%USERPROFILE%\Tradebot"
echo Hedef klasor: %KURDIZIN%
echo.

REM ---- 1) Python var mi? ----
set "PYOK="
py -3 -c "1" >nul 2>nul
if not errorlevel 1 set "PYOK=1"
if not defined PYOK (
  python -c "1" >nul 2>nul
  if not errorlevel 1 set "PYOK=1"
)
if not defined PYOK if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PYOK=1"

if defined PYOK (
  echo [1/4] Python zaten var, geciliyor.
) else (
  echo [1/4] Python indiriliyor... ^(25 MB^)
  curl -L -o "%TEMP%\py311.exe" https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe
  if errorlevel 1 goto neterr
  echo        Python kuruluyor, 1-2 dakika bekle...
  start /wait "" "%TEMP%\py311.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_launcher=1
  echo        Python kuruldu.
)

REM ---- 2) Botu indir ----
echo [2/4] Bot GitHub'dan indiriliyor...
curl -L -o "%TEMP%\tradebot.zip" https://github.com/exxodia67/Tradebot/archive/refs/heads/main.zip
if errorlevel 1 goto neterr

REM ---- 3) Ac ve yerlestir (.env ve eski veriler korunur) ----
echo [3/4] Dosyalar yerlestiriliyor...
if exist "%TEMP%\tradebot_zip" rmdir /s /q "%TEMP%\tradebot_zip"
powershell -NoProfile -Command "Expand-Archive -Force '%TEMP%\tradebot.zip' '%TEMP%\tradebot_zip'"
if not exist "%KURDIZIN%" mkdir "%KURDIZIN%"
robocopy "%TEMP%\tradebot_zip\Tradebot-main" "%KURDIZIN%" /E /NFL /NDL /NJH /NJS >nul

REM ---- 4) Kurulum + baslatmayi ic bat'a devret ----
echo [4/4] Kurulum ve baslatma...
echo.
cd /d "%KURDIZIN%"
call KUR-VE-CALISTIR.bat
goto :eof

:neterr
echo.
echo [HATA] Indirme basarisiz. Internet baglantisini kontrol edip tekrar calistir.
pause
