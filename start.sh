#!/bin/bash
# Email AI Agent - 启动脚本（Linux/Mac）

echo "========================================"
echo "Email AI Agent - 启动中..."
echo "========================================"
echo ""

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "[错误] 未找到 Python，请先安装 Python 3.7+"
    exit 1
fi

# 检查依赖
echo "[1/3] 检查 Python 依赖..."
if ! python3 -c "import flask" 2>/dev/null; then
    echo "[提示] 正在安装 Python 依赖..."
    pip3 install -r requirements.txt
fi

# 检查前端构建
echo "[2/3] 检查前端构建..."
if [ ! -f "src/email_agent/web/dist/index.html" ]; then
    echo "[提示] 前端未构建，将使用开发模式"
    echo "[提示] 如需使用 Vue3 前端，请运行："
    echo "         cd frontend"
    echo "         npm install"
    echo "         npm run build"
    echo ""
fi

# 启动服务
echo "[3/3] 启动 Web 服务..."
echo ""
echo "========================================"
echo "服务已启动！"
echo ""
echo "Vue3 前端（新）: http://127.0.0.1:8765"
echo "Flask 界面（旧）: http://127.0.0.1:8765/mail"
echo ""
echo "默认账号: admin / admin123"
echo "========================================"
echo ""

python3 web_app.py
