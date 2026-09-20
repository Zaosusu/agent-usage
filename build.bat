@echo off
REM ============================================================
REM  Agent Token Monitor - Windows 打包脚本
REM  用法：双击运行，或在命令行执行 build.bat
REM  产物：dist\AgentTokenMonitor.exe（单文件，双击即用）
REM ============================================================
cd /d "%~dp0"

echo [1/3] 检查依赖...
python -m pip show pyinstaller >nul 2>&1 || python -m pip install pyinstaller

echo [2/3] 清理旧构建...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [3/3] 打包（web + 内置插件 一并打入）...
python -m PyInstaller --noconfirm --clean --onefile --console ^
  --name AgentTokenMonitor ^
  --add-data "web;web" ^
  --add-data "plugins;plugins_internal" ^
  --exclude-module pandas ^
  --exclude-module numpy ^
  app.py

echo.
echo 完成！产物：dist\AgentTokenMonitor.exe
echo 说明：复制 exe 到任意目录双击即可运行；
echo       如需扩展新 Agent，在 exe 同目录建 plugins\ 文件夹放入插件文件即可。
pause
