@echo off
REM 打包脚本 / Build script (Windows)
REM 用法: 双击运行，或在项目根目录执行 build.cmd
REM 产物: dist\CDiskCleaner.exe (独立单文件 / standalone one-file)
cd /d "%~dp0"
set "PY=%~dp0venv\Scripts\python.exe"

if not exist "%PY%" (
  echo [ERROR] venv 未找到，请先创建虚拟环境并 pip install -r requirements.txt
  echo [ERROR] venv not found. Create it and run: pip install -r requirements.txt
  pause
  exit /b 1
)

"%PY%" -m PyInstaller ^
  --noconsole ^
  --onefile ^
  --name CDiskCleaner ^
  --icon "src\gui\assets\icon.ico" ^
  --add-data "src\gui;gui" ^
  --hidden-import webview ^
  src/main.py

echo.
if exist "dist\CDiskCleaner.exe" (
  echo [OK] 打包完成 / Build done: dist\CDiskCleaner.exe
) else (
  echo [FAIL] 打包失败，请查看上方输出 / Build failed, see output above.
)
pause
