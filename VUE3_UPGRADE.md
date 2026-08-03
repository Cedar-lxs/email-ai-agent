# Vue3 前端重构 - 更新说明

## 更新概览

本次更新将原有的 Flask 模板界面重构为现代化的 Vue3 + Element Plus 单页应用，并增加了用户认证功能。

### 主要变化

#### 1. **前端技术栈升级** ✅
- **原技术栈**: Flask Jinja2 模板 + 原生 CSS
- **新技术栈**: Vue3 + Element Plus + Vite
- **优势**:
  - 现代化 UI 组件库
  - 更好的交互体验
  - 前后端分离架构
  - 易于维护和扩展

#### 2. **用户认证系统** ✅
- JWT Token 认证机制
- 自动登录状态管理
- Token 过期自动处理
- 默认账号: `admin` / `admin123`

#### 3. **RESTful API** ✅
- 新增 `/api` 路由前缀
- 标准化的 JSON 响应
- Token 认证中间件
- CORS 支持

#### 4. **向后兼容** ✅
- 保留原有 Flask 模板路由
- 旧界面访问地址: `http://127.0.0.1:8765/mail`
- 新界面访问地址: `http://127.0.0.1:8765`

## 新增文件清单

### 前端文件 (`frontend/`)
```
frontend/
├── package.json                      # 前端依赖配置
├── vite.config.js                    # Vite 构建配置
├── index.html                        # HTML 入口
├── README.md                         # 前端使用文档
└── src/
    ├── main.js                       # Vue 入口
    ├── App.vue                       # 根组件
    ├── api/
    │   ├── auth.js                   # 认证 API
    │   └── mail.js                   # 邮件 API
    ├── router/
    │   └── index.js                  # 路由配置
    ├── store/
    │   └── auth.js                   # 认证状态
    ├── utils/
    │   └── request.js                # Axios 封装
    └── views/
        ├── Login.vue                 # 登录页
        ├── Layout.vue                # 主布局
        ├── MailList.vue              # 邮件列表
        ├── MailDetail.vue            # 邮件详情
        ├── Knowledge.vue             # 知识库（待开发）
        └── Settings.vue              # 设置（待开发）
```

### 后端文件
```
src/email_agent/web/
├── auth.py                           # 认证中间件（新增）
└── routes/
    └── api.py                        # RESTful API（新增）
```

### 辅助脚本
```
build-frontend.bat                    # Windows 前端构建脚本
start.bat                             # Windows 启动脚本
start.sh                              # Linux/Mac 启动脚本
```

## 快速开始

### 方式一：完整安装（推荐）

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

访问: http://127.0.0.1:8765

### 方式二：使用便捷脚本（Windows）

```bash
# 构建前端
build-frontend.bat

# 启动服务
start.bat
```

### 方式三：开发模式（前后端分离）

```bash
# 终端 1：启动后端
python web_app.py

# 终端 2：启动前端开发服务器
cd frontend
npm run dev
```

前端: http://localhost:5173
后端: http://localhost:8765

## 界面对比

### 旧界面 (Flask 模板)
- 地址: http://127.0.0.1:8765/mail
- 技术: Jinja2 模板 + 原生 CSS
- 特点: 简单直接，无需构建

### 新界面 (Vue3 SPA)
- 地址: http://127.0.0.1:8765
- 技术: Vue3 + Element Plus
- 特点: 现代化、响应式、更好的用户体验

### 功能对比

| 功能 | 旧界面 | 新界面 |
|-----|--------|--------|
| 用户认证 | ❌ | ✅ |
| 邮件列表 | ✅ | ✅ |
| 邮件详情 | ✅ | ✅ |
| 并排对比 | ✅ | ✅（优化） |
| 草稿编辑 | ✅ | ✅ |
| 批准/拒绝 | ✅ | ✅ |
| 知识库管理 | ✅ | 🚧（规划中） |
| 响应式设计 | ⚠️ | ✅ |
| 现代化 UI | ⚠️ | ✅ |

## API 文档

### 认证接口

#### POST /api/auth/login
登录获取 Token

**请求**:
```json
{
  "username": "admin",
  "password": "admin123"
}
```

**响应**:
```json
{
  "token": "eyJhbGc...",
  "username": "admin",
  "message": "登录成功"
}
```

#### GET /api/auth/verify
验证 Token 有效性

**Headers**: `Authorization: Bearer {token}`

**响应**:
```json
{
  "username": "admin",
  "message": "认证有效"
}
```

### 邮件接口

所有邮件接口需要在 Header 中携带 Token:
```
Authorization: Bearer {token}
```

#### GET /api/mails
获取邮件列表

**参数**:
- `status`: 邮件状态（draft_ready, replied, escalated, rejected, all）
- `q`: 搜索关键词
- `page`: 页码（默认 1）
- `page_size`: 每页数量（默认 20）

#### GET /api/mails/:id
获取邮件详情

#### POST /api/mails/:id/save
保存草稿

#### POST /api/mails/:id/approve
批准并发送

#### POST /api/mails/:id/reject
拒绝草稿

#### POST /api/mails/delete
批量删除

#### GET /api/mails/stats
获取统计信息

## 安全性

### 认证机制
- 使用 JWT Token 机制
- Token 有效期 24 小时
- 密码使用 SHA-256 哈希存储
- 支持自动登录状态保持

### 默认账号
```
用户名: admin
密码: admin123
```

### 修改密码

生成新密码哈希:
```python
import hashlib
password = "your_new_password"
hash_value = hashlib.sha256(password.encode()).hexdigest()
print(hash_value)
```

修改 `src/email_agent/web/auth.py`:
```python
class AuthManager:
    USERS = {
        "admin": {
            "password_hash": "你的新密码哈希",
            "username": "admin"
        }
    }
```

### 生产环境建议
1. ⚠️ **务必修改默认密码**
2. 使用 HTTPS
3. 配置 Token 存储到 Redis（当前使用内存）
4. 启用访问日志
5. 配置 IP 白名单或 VPN
6. 定期更新依赖

## 依赖更新

### Python 依赖
新增 `flask-cors` 用于 CORS 支持:
```
pip install flask-cors
```

或通过 requirements.txt:
```
pip install -r requirements.txt
```

### Node.js 依赖
前端需要 Node.js 16+ 和 npm:
```bash
cd frontend
npm install
```

## 配置说明

### 前端配置 (`frontend/vite.config.js`)
```javascript
export default defineConfig({
  server: {
    port: 5173,              // 开发服务器端口
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',  // 后端地址
        changeOrigin: true
      }
    }
  },
  build: {
    outDir: '../src/email_agent/web/dist'  // 构建输出目录
  }
})
```

### 后端配置
无需额外配置，自动集成前端构建产物。

## 目录结构变化

```
email-ai-agent/
├── frontend/                    # 新增：Vue3 前端项目
│   ├── src/
│   ├── package.json
│   └── vite.config.js
├── src/
│   └── email_agent/
│       └── web/
│           ├── dist/            # 新增：前端构建产物
│           ├── auth.py          # 新增：认证模块
│           ├── app.py           # 修改：集成 API 和 SPA
│           └── routes/
│               └── api.py       # 新增：RESTful API
├── build-frontend.bat           # 新增：构建脚本
├── start.bat                    # 新增：启动脚本
├── start.sh                     # 新增：启动脚本
└── requirements.txt             # 修改：增加 flask-cors
```

## 故障排除

### Q: 前端构建失败？
```bash
# 检查 Node.js 版本（需要 16+）
node -v

# 清理并重新安装
cd frontend
rm -rf node_modules package-lock.json
npm install
npm run build
```

### Q: 登录失败？
- 检查用户名密码是否正确（默认 admin/admin123）
- 查看浏览器控制台错误信息
- 检查后端日志

### Q: 访问 API 出现 CORS 错误？
- 确保已安装 flask-cors: `pip install flask-cors`
- 开发模式下使用 Vite 代理，不会有 CORS 问题

### Q: 旧界面无法访问？
旧界面仍然可用，访问地址: http://127.0.0.1:8765/mail

### Q: Token 过期如何处理？
Token 默认 24 小时有效，过期后会自动跳转到登录页。

## 迁移指南

### 从旧界面迁移
1. 旧界面和新界面可以共存
2. 数据库和后端逻辑无变化
3. 可以随时在两个界面间切换

### 数据兼容性
- 完全兼容现有数据库
- 所有功能保持一致
- 无需数据迁移

## 后续计划

### 短期
- [ ] 完善知识库管理界面
- [ ] 完善系统设置界面
- [ ] 添加更多统计图表
- [ ] 移动端优化

### 中期
- [ ] 多用户角色管理
- [ ] 权限控制系统
- [ ] 实时通知功能
- [ ] 操作审计日志

### 长期
- [ ] 国际化支持
- [ ] 主题定制
- [ ] 插件系统
- [ ] API 开放平台

## 反馈和支持

如有问题或建议，请通过以下方式反馈：
- 提交 Issue
- 查看文档: `frontend/README.md`
- 查看日志: `logs/email_agent.log`

## 版本历史

### v2.0.0 (2026-07-30)
- ✅ Vue3 + Element Plus 前端重构
- ✅ 用户认证系统
- ✅ RESTful API
- ✅ 并排对比优化
- ✅ 响应式设计

### v1.0.0 (之前)
- ✅ Flask 模板界面
- ✅ 邮件自动处理
- ✅ 知识库检索
- ✅ 草稿审核流程
