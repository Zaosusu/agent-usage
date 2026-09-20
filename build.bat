@echo off
REM ============================================================
REM  agent-usage - Windows build script
REM  Usage: double-click or run build.bat in terminal
REM  Output: dist\agent-usage.exe (single file)
REM ============================================================
cd /d "%~dp0"

echo [1/3] Checking dependencies...
python -m pip show pyinstaller >nul 2>&1 || python -m pip install pyinstaller

echo [2/3] Cleaning old build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [3/3] Building (web + plugins bundled)...
python -m PyInstaller --noconfirm --clean --onefile --console ^
  --name agent-usage ^
  --add-data "web;web" ^
  --add-data "plugins;plugins_internal" ^
  --exclude-module pandas ^
  --exclude-module numpy ^
  app.py

echo.
echo Done! Output: dist\agent-usage.exe
echo Copy the exe anywhere and double-click to run.
pause
