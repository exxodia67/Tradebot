@echo off
cd /d "%~dp0"
echo ============================================
echo  Tradebot Dashboard baslatiliyor...
echo  Tarayici birazdan kendiliginden acilacak.
echo  Kapatmak icin bu pencereyi kapat veya Ctrl+C.
echo ============================================
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8000"
.venv\Scripts\python.exe -m tradebot.web.app
echo.
echo Dashboard durdu. Cikmak icin bir tusa bas.
pause >nul
