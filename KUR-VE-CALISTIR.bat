@echo off
chcp 65001 >nul
title Tradebot - Kur ve Calistir
cd /d "%~dp0"

REM ============================================================
REM  TEK DOSYA: baska bir PC'de bunu cift tikla, gerisi otomatik.
REM  Python'u PATH'te olmasa bile kendisi bulur.
REM ============================================================

set "PYEXE="
set "PYARG="

REM 1) py launcher (python.org kurulumuyla gelir, PATH derdi yok)
py -3 -c "1" >nul 2>nul
if not errorlevel 1 (
  set "PYEXE=py"
  set "PYARG=-3"
  goto bulundu
)

REM 2) PATH'teki python (Store sahte-alias'ini ele: gercekten calisiyor mu?)
python -c "1" >nul 2>nul
if not errorlevel 1 (
  set "PYEXE=python"
  goto bulundu
)

REM 3) Bilinen kurulum klasorleri
for %%V in (311 312 313 310) do (
  if exist "%LocalAppData%\Programs\Python\Python%%V\python.exe" (
    set "PYEXE=%LocalAppData%\Programs\Python\Python%%V\python.exe"
    goto bulundu
  )
  if exist "C:\Program Files\Python%%V\python.exe" (
    set "PYEXE=C:\Program Files\Python%%V\python.exe"
    goto bulundu
  )
)

echo [HATA] Python hicbir yerde bulunamadi. Su komutla kur (CMD'ye yapistir):
echo winget install -e --id Python.Python.3.11 --override "/quiet InstallAllUsers=0 PrependPath=1"
echo Sonra bu dosyayi TEKRAR calistir.
pause
exit /b 1

:bulundu
echo Python bulundu: %PYEXE%

if exist .venv if not exist .venv\Scripts\python.exe (
  echo Bozuk .venv bulundu, siliniyor...
  rmdir /s /q .venv
)
if not exist .venv (
  echo [1/3] Sanal ortam kuruluyor...
  "%PYEXE%" %PYARG% -m venv .venv
  if errorlevel 1 (
    echo [HATA] venv kurulamadi.
    pause
    exit /b 1
  )
)

echo [2/3] Bagimliliklar kuruluyor... (ilk sefer 1-2 dk surer)
.venv\Scripts\python -m pip install --upgrade pip >nul 2>nul
.venv\Scripts\python -m pip install -e . >kurulum_log.txt 2>&1
if errorlevel 1 (
  echo [HATA] Kurulum basarisiz. Hata dokumu:
  echo ------------------------------------------------
  type kurulum_log.txt
  echo ------------------------------------------------
  echo Bu ekranin fotosunu at / kurulum_log.txt dosyasini gonder.
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
