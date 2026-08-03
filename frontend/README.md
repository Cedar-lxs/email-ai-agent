# Vue3 前端使用指南

## 功能特性

### 1. 现代化 UI
- ✅ Vue3 + Element Plus 组件库
- ✅ 响应式设计，支持移动端
- ✅ 流畅的动画和交互体验

### 2. 用户认证
- ✅ JWT Token 认证
- ✅ 自动登录状态管理
- ✅ Token 自动续期
- ✅ 默认账号：admin / admin123

### 3. 邮件管理
- ✅ 邮件列表（搜索、筛选、分页）
- ✅ 邮件详情（并排对比原文和草稿）
- ✅ 草稿编辑和保存
- ✅ 批准发送或拒绝
- ✅ 知识依据展示

## 快速开始

### 安装依赖

```bash
# 安装 Python 依赖
pip install -r requirements.txt

# 安装前端依赖
cd frontend
npm install
```

### 开发模式

**方式一：前后端分离开发（推荐）**

```bash
# 终端 1：启动后端 API 服务器
python web_app.py

# 终端 2：启动前端开发服务器
cd frontend
npm run dev
```

前端开发服务器：http://localhost:5173
后端 API 服务器：http://localhost:8765

**方式二：构建后集成**

```bash
# 构建前端
cd frontend
npm run build

# 启动后端（自动服务前端静态文件）
cd ..
python web_app.py
```

访问：http://localhost:8765

### 生产部署

```bash
# 1. 构建前端
cd frontend
npm run build

# 2. 启动后端
cd ..
python web_app.py
```

## 目录结构

```
frontend/
├── src/
│   ├── api/          # API 服务层
│   │   ├── auth.js   # 认证 API
│   │   └── mail.js   # 邮件 API
│   ├── components/   # 公共组件
│   ├── router/       # 路由配置
│   │   └── index.js
│   ├── store/        # 状态管理
│   │   └── auth.js   # 认证状态
│   ├── utils/        # 工具函数
│   │   └── request.js # Axios 封装
│   ├── views/        # 页面组件
│   │   ├── Login.vue
│   │   ├── Layout.vue
│   │   ├── MailList.vue
│   │   ├── MailDetail.vue
│   │   ├── Knowledge.vue
│   │   └── Settings.vue
│   ├── App.vue       # 根组件
│   └── main.js       # 入口文件
├── public/           # 静态资源
├── index.html        # HTML 模板
├── vite.config.js    # Vite 配置
└── package.json      # 依赖配置
```

## API 接口

### 认证接口

#### 登录
```
POST /api/auth/login
Content-Type: application/json

{
  "username": "admin",
  "password": "admin123"
}

响应：
{
  "token": "xxx",
  "username": "admin",
  "message": "登录成功"
}
```

#### 验证 Token
```
GET /api/auth/verify
Authorization: Bearer {token}

响应：
{
  "username": "admin",
  "message": "认证有效"
}
```

#### 登出
```
POST /api/auth/logout
Authorization: Bearer {token}

响应：
{
  "message": "已登出"
}
```

### 邮件接口

#### 获取邮件列表
```
GET /api/mails?status=draft_ready&q=&page=1&page_size=20
Authorization: Bearer {token}

响应：
{
  "mails": [...],
  "total": 100,
  "page": 1,
  "page_size": 20
}
```

#### 获取邮件详情
```
GET /api/mails/{message_id}
Authorization: Bearer {token}

响应：
{
  "mail": {...},
  "draft_body": "...",
  "retrieval": {...},
  "reply_subject": "Re: ..."
}
```

#### 保存草稿
```
POST /api/mails/{message_id}/save
Authorization: Bearer {token}
Content-Type: application/json

{
  "body": "更新的回复内容"
}
```

#### 批准发送
```
POST /api/mails/{message_id}/approve
Authorization: Bearer {token}
```

#### 拒绝草稿
```
POST /api/mails/{message_id}/reject
Authorization: Bearer {token}
Content-Type: application/json

{
  "reason": "拒绝原因"
}
```

#### 删除邮件
```
POST /api/mails/delete
Authorization: Bearer {token}
Content-Type: application/json

{
  "message_ids": ["id1", "id2"]
}
```

#### 获取统计
```
GET /api/mails/stats
Authorization: Bearer {token}

响应：
{
  "counts": {
    "draft_ready": 10,
    "replied": 20,
    "escalated": 5
  },
  "mode": "semi_auto"
}
```

## 用户管理

### 修改默认密码

编辑 `src/email_agent/web/auth.py`：

```python
class AuthManager:
    USERS = {
        "admin": {
            "password_hash": "你的密码哈希",
            "username": "admin"
        }
    }
```

生成密码哈希：

```python
import hashlib
password = "your_password"
hash_value = hashlib.sha256(password.encode()).hexdigest()
print(hash_value)
```

### 添加新用户

在 `AuthManager.USERS` 中添加：

```python
"newuser": {
    "password_hash": "密码哈希",
    "username": "newuser"
}
```

## 技术栈

### 前端
- Vue 3.4
- Element Plus 2.5
- Vue Router 4.2
- Pinia 2.1
- Axios 1.6
- Vite 5.0

### 后端
- Flask 3.0
- Flask-CORS 4.0
- 内存 Token 存储（可扩展为 Redis）

## 常见问题

### Q: 前端访问后端 API 跨域问题？
A: 开发模式下 Vite 已配置代理，生产模式前后端同源无跨域问题。

### Q: Token 存储在哪里？
A: 目前使用内存存储，重启后端会失效。生产环境建议使用 Redis 或数据库。

### Q: 如何自定义前端主题？
A: 修改 Element Plus 主题变量，或在组件中使用 CSS 变量覆盖。

### Q: 前端路由刷新 404？
A: 生产模式下已配置 SPA 路由回退，刷新会正常工作。

### Q: 如何集成到现有系统？
A: 可以保留原有 Flask 模板路由，新旧界面共存。访问 `/mail` 使用旧界面，`/` 使用新界面。

## 安全建议

1. **生产环境必须修改默认密码**
2. **使用 HTTPS**
3. **配置更强的 Token 过期策略**
4. **实施 IP 白名单或 VPN 访问**
5. **定期更新依赖包**
6. **启用访问日志审计**

## 后续计划

- [x] 知识库管理界面（文件上传、批量删除、索引状态与手动重建）
- [x] 系统设置界面（运行模式、配置概览、邮箱与 AI 连接测试）
- [ ] 更多用户角色和权限
- [ ] 实时通知功能
- [ ] 数据统计和可视化
- [ ] 国际化支持

## 许可证

与主项目相同
