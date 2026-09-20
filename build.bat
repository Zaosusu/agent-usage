@echo off
REM ============================================================
REM  Agent Token Monitor - Windows 鎵撳寘鑴氭湰
REM  鐢ㄦ硶锛氬弻鍑昏繍琛岋紝鎴栧湪鍛戒护琛屾墽琛?build.bat
REM  浜х墿锛歞ist\agent-usage.exe锛堝崟鏂囦欢锛屽弻鍑诲嵆鐢級
REM ============================================================
cd /d "%~dp0"

echo [1/3] 妫€鏌ヤ緷璧?..
python -m pip show pyinstaller >nul 2>&1 || python -m pip install pyinstaller

echo [2/3] 娓呯悊鏃ф瀯寤?..
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [3/3] 鎵撳寘锛坵eb + 鍐呯疆鎻掍欢 涓€骞舵墦鍏ワ級...
python -m PyInstaller --noconfirm --clean --onefile --console ^
  --name agent-usage ^
  --add-data "web;web" ^
  --add-data "plugins;plugins_internal" ^
  --exclude-module pandas ^
  --exclude-module numpy ^
  app.py

echo.
echo 瀹屾垚锛佷骇鐗╋細dist\agent-usage.exe
echo 璇存槑锛氬鍒?exe 鍒颁换鎰忕洰褰曞弻鍑诲嵆鍙繍琛岋紱
echo       濡傞渶鎵╁睍鏂?Agent锛屽湪 exe 鍚岀洰褰曞缓 plugins\ 鏂囦欢澶规斁鍏ユ彃浠舵枃浠跺嵆鍙€?pause


