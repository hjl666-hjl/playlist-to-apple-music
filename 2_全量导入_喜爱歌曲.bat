@echo off
chcp 65001 >nul
set PYTHONUTF8=1
python "%~dp0import_to_apple_music.py" --mode favorite
echo.
pause
