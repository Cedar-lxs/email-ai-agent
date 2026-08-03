# Email AI Agent - 快速使用指南

## 🚀 新功能速览

### Vue3 前端重构 ✅
- 现代化 UI（Vue3 + Element Plus）
- 响应式设计，支持移动端
- 流畅的交互体验

### 用户认证 ✅
- JWT Token 认证
- 安全的密码哈希存储
- 自动登录状态管理
- **默认账号**: `admin` / `admin123`

### 并排对比优化 ✅
- 原文和草稿左右并排显示
- 统一高度，便于对比
- 优化的交互体验

## 📦 安装和启动

### 方式一：一键启动（Windows 推荐）

```bash
# 1. 构建前端
build-frontend.bat

# 2. 启动服务
start.bat
```

### 方式二：手动安装

```bash
# 1. 安装 Python 依赖
pip install -r requirements.txt

# 2. 安装前端依赖并构建
cd frontend
npm install
npm run build
cd ..

# 3. 启动服务
python web_app.py
```

### 方式三：开发模式（前后端分离）

```bash
# 终端 1：启动后端
python web_app.py

# 终端 2：启动前端开发服务器
cd frontend
npm run dev
```

## 🌐 访问地址

启动成功后，可以通过以下地址访问：

- **Vue3 新界面**（推荐）: http://127.0.0.1:8765
- **Flask 旧界面**（兼容）: http://127.0.0.1:8765/mail

## 🔐 登录信息

```
用户名: admin
密码: admin123
```

⚠️ **生产环境务必修改默认密码！**

## 📚 主要功能

### 1. 邮件列表
- 按状态筛选（待审核、已发送、转人工等）
- 搜索邮件主题和发件人
- 分页浏览
- 点击查看详情

### 2. 邮件详情
- **并排对比**: 原文和草稿左右显示
- **编辑草稿**: 实时保存修改
- **知识依据**: 展示匹配的知识库内容（置信度）
- **操作**: 批准发送、拒绝、删除

### 3. 认证系统
- Token 自动续期
- 登出功能
- 自动跳转登录页

## 🔧 修改密码

### 生成新密码哈希

```python
import hashlib
password = "your_new_password"
hash_value = hashlib.sha256(password.encode()).hexdigest()
print(hash_value)
```

### 更新配置

编辑 `src/email_agent/web/auth.py`:

```python
class AuthManager:
    USERS = {
        "admin": {
            "password_hash": "你的新密码哈希",
            "username": "admin"
        }
    }
```

## 📁 项目结构

```
email-ai-agent/
├── frontend/                    # Vue3 前端项目
│   ├── src/
│   │   ├── api/                # API 服务层
│   │   ├── components/         # 公共组件
│   │   ├── router/             # 路由配置
│   │   ├── store/              # 状态管理
│   │   ├── utils/              # 工具函数
│   │   └── views/              # 页面组件
│   ├── package.json
│   └── vite.config.js
├── src/
│   └── email_agent/
│       ├── application/        # 业务逻辑
│       ├── domain/             # 领域模型
│       ├── infrastructure/     # 基础设施
│       └── web/                # Web 应用
│           ├── dist/           # 前端构建产物
│           ├── auth.py         # 认证模块
│           └── routes/
│               └── api.py      # RESTful API
├── logs/                       # 日志文件
├── knowledge/                  # 知识库文件
├── data/                       # 数据库
├── build-frontend.bat          # 前端构建脚本
├── start.bat                   # 启动脚本（Windows）
├── start.sh                    # 启动脚本（Linux/Mac）
├── config.yaml                 # 主配置文件
├── requirements.txt            # Python 依赖
├── VUE3_UPGRADE.md            # Vue3 升级文档
└── README.md                   # 主文档
```

## 🐛 常见问题

### Q: 前端显示空白页？
A: 检查是否已构建前端：
```bash
cd frontend
npm install
npm run build
```

### Q: 登录失败？
A: 
1. 检查用户名密码是否正确（默认 admin/admin123）
2. 查看浏览器控制台错误信息
3. 检查后端日志 `logs/email_agent.log`

### Q: API 请求失败？
A:
1. 确认后端服务已启动
2. 检查 Token 是否有效
3. 查看网络请求状态码

### Q: 如何同时使用新旧界面？
A: 两个界面可以共存：
- 新界面: http://127.0.0.1:8765
- 旧界面: http://127.0.0.1:8765/mail

## 📖 详细文档

- **前端文档**: `frontend/README.md`
- **Vue3 升级说明**: `VUE3_UPGRADE.md`
- **更新日志**: `CHANGELOG.md`

## ✨ 技术栈

### 前端
- Vue 3.4
- Element Plus 2.5
- Vue Router 4.2
- Pinia 2.1
- Axios 1.6
- Vite 5.0

### 后端
- Python 3.7+
- Flask 3.0
- Flask-CORS 4.0
- httpx (异步支持)
- SQLite

## 🔒 安全建议

1. ✅ **修改默认密码**（生产环境必须）
2. ✅ **使用 HTTPS**
3. ✅ **配置 IP 白名单或 VPN**
4. ✅ **定期更新依赖包**
5. ✅ **启用访问日志审计**
6. ⚠️ **Token 存储**: 当前使用内存，建议生产环境使用 Redis

## 🎯 下一步

### 立即开始
1. 运行 `build-frontend.bat` 构建前端
2. 运行 `start.bat` 启动服务
3. 访问 http://127.0.0.1:8765
4. 使用 `admin/admin123` 登录

### 配置邮箱
1. 编辑 `.env` 文件
2. 填写邮箱账号和 API 密钥
3. 运行 `python main.py once` 测试

### 添加知识库
1. 将 Markdown 文档放入 `knowledge/` 目录
2. 运行 `python main.py rag-build` 构建索引
3. 在 Web 界面查看知识库

## 📞 技术支持

如有问题或建议，请：
1. 查看详细文档
2. 检查日志文件 `logs/email_agent.log`
3. 提交 Issue 或反馈

---

**祝使用愉快！** 🎉
