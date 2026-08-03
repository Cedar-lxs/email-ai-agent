@echo off
REM Email AI Agent - 启动脚本（Windows）

echo ========================================
echo Email AI Agent - 启动中...
echo ========================================
echo.

REM 检查 Python
where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [错误] 未找到 Python，请先安装 Python 3.7+
    pause
    exit /b 1
)

REM 检查依赖
echo [1/3] 检查 Python 依赖...
python -c "import flask, flask_cors" >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [提示] 正在安装 Python 依赖...
    python -m pip install -r requirements.txt
)

REM 检查前端构建
echo [2/3] 检查前端构建...
if not exist "src\email_agent\web\dist\index.html" (
    echo [提示] 前端未构建，将使用开发模式
    echo [提示] 如需使用 Vue3 前端，请运行：
    echo          cd frontend
    echo          npm install
    echo          npm run build
    echo.
)

REM 启动服务
echo [3/3] 启动 Web 服务...
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    echo [提示] Web 服务已在 http://127.0.0.1:8765 运行，无需重复启动。
    echo.
    pause
    exit /b 0
)
echo.
echo ========================================
echo 服务已启动！
echo.
echo Vue3 前端（新）: http://127.0.0.1:8765
echo Flask 界面（旧）: http://127.0.0.1:8765/mail
echo.
echo 默认账号: admin / admin123
echo ========================================
echo.

python web_app.py

pause
