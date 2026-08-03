@echo off
REM 前端安装和构建脚本 - 使用 CMD 避免 PowerShell 执行策略问题

echo ========================================
echo 安装和构建 Vue3 前端...
echo ========================================
echo.

cd /d d:\email-ai-agent\frontend

REM 检查 Node.js
where node >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [错误] 未找到 Node.js，请先安装 Node.js 16+
    echo 下载地址: https://nodejs.org/
    pause
    exit /b 1
)

echo [1/2] 安装依赖...
call npm install
if %ERRORLEVEL% NEQ 0 (
    echo [错误] npm install 失败
    pause
    exit /b 1
)

echo.
echo [2/2] 构建前端...
call npm run build
if %ERRORLEVEL% NEQ 0 (
    echo [错误] 构建失败
    pause
    exit /b 1
)

echo.
echo ========================================
echo 构建完成！
echo.
echo 前端已构建到: ..\src\email_agent\web\dist
echo.
echo 现在刷新浏览器访问: http://127.0.0.1:8765
echo ========================================
echo.

cd ..
pause
