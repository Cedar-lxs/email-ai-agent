@echo off
REM 前端构建脚本（Windows）

echo ========================================
echo 构建 Vue3 前端...
echo ========================================
echo.

cd frontend

REM 检查 Node.js
where node >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [错误] 未找到 Node.js，请先安装 Node.js 16+
    echo 下载地址: https://nodejs.org/
    pause
    exit /b 1
)

REM 检查依赖
echo [1/3] 检查 npm 依赖...
if not exist "node_modules" (
    echo [提示] 正在安装依赖...
    call npm install
    if %ERRORLEVEL% NEQ 0 (
        echo [错误] npm install 失败
        pause
        exit /b 1
    )
)

REM 构建
echo [2/3] 构建前端...
call npm run build
if %ERRORLEVEL% NEQ 0 (
    echo [错误] 构建失败
    pause
    exit /b 1
)

REM 完成
echo [3/3] 构建完成！
echo.
echo ========================================
echo 前端已构建到: src\email_agent\web\dist
echo.
echo 现在可以运行后端服务器：
echo   python web_app.py
echo.
echo 然后访问: http://127.0.0.1:8765
echo ========================================

cd ..
pause
