# Email AI Agent - 快速使用指南

## 🚀 新功能速览

### Vue3 前端重构 ✅
- 现代化 UI（Vue3 + Element Plus）
- 响应式设计，支持移动端
- 流畅的交互体验

### 用户认证 ✅
- 首次访问时创建本机管理员，不提供默认密码
- `scrypt` 随机盐密码哈希
- HttpOnly Cookie 和 CSRF 双重保护
- 登录失败锁定、持久会话和设置页改密

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

## 🔐 首次登录

首次打开工作台会进入“创建管理员”页面。用户名可自行设置；密码至少 12 位，并包含大小写字母、数字和符号中的至少三类。系统不再提供默认密码。

### 沿用旧版本业务数据

如果新版安装在另一个目录，可在新版 `.env` 中指定旧版本目录：

```env
EMAIL_AGENT_RUNTIME_ROOT=D:\email-ai-agent
```

新版会继续使用该目录下的 `data`、`drafts` 和 `logs`。首次启动时会在原
邮件数据库中原地补充认证表，不会清空已有邮件。同目录升级不需要设置此项。

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
- **处理决策**: 展示自动发送、草稿或转人工的原因
- **附件内容**: 展示普通附件、提取状态、文本预览和下载入口
- **操作**: 批准发送、拒绝、删除

### 3. 认证系统
- 12 小时持久会话
- 连续失败 5 次锁定 15 分钟
- 登出功能
- 设置页修改密码并注销全部旧会话
- 自动跳转登录页

### 4. 多模态售后判断
- 可选接入 DeepSeek 视觉模型
- 支持附件图片、正文内嵌图片和视频关键帧
- 识别摘要会显示在邮件详情页，供人工审核参考
- 发现烧毁、进水、裸线、拆机、电源异常等风险信号会转人工
- 识别交换机标签里的型号、设备 ID、端口组成、Ver 和管理/非管理标识
- 与产品索引冲突时不会自动发送

### 5. 普通附件识别
- 支持保存 PDF、DOCX、XLSX、CSV/TXT 附件
- 提取到的文本会参与本地知识库匹配和回复生成
- 压缩包只保存文件信息，不自动解压
- 附件提取失败会生成草稿等待审核，不会全自动发送
- 博查 Web Search 不会接收附件正文

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
1. 首次使用时先按页面提示创建管理员
2. 连续失败 5 次后等待 15 分钟再试
3. 查看浏览器提示和后端日志 `logs/email_agent.log`

### Q: API 请求失败？
A:
1. 确认后端服务已启动
2. 重新登录，确认会话仍然有效
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
- httpx (异步支持)
- SQLite

## 🔒 安全建议

1. ✅ **首次启动创建强密码**
2. ✅ **使用 HTTPS**
   - 通过反向代理提供 HTTPS 时，如后端无法识别安全连接，请设置 `EMAIL_AGENT_SECURE_COOKIES=true`
3. ✅ **配置 IP 白名单或 VPN**
4. ✅ **定期更新依赖包**
5. ✅ **启用访问日志审计**
6. ✅ **会话存储**: 仅在数据库保存令牌哈希，浏览器使用 HttpOnly Cookie

## 🎯 下一步

### 立即开始
1. 运行 `build-frontend.bat` 构建前端
2. 运行 `start.bat` 启动服务
3. 访问 http://127.0.0.1:8765
4. 按页面提示创建管理员并登录

### 配置邮箱
1. 编辑 `.env` 文件
2. 填写邮箱账号和 API 密钥
3. 运行 `python main.py once` 测试

### 开启多模态识别
1. 在 `.env` 中填写 `MULTIMODAL_API_KEY`，为空时复用 `AI_API_KEY`
2. 在 `config.yaml` 中设置 `multimodal.enabled: true`
3. 系统会读取交换机标签中的型号、设备 ID、端口组成、Ver 和管理型/非管理型标识
4. 视频会抽取关键帧后识别，不会直接上传完整视频
5. 公网图片 URL 暂不自动下载，只处理附件和正文内嵌图片
6. 若知识库无答案，低风险问题可使用博查补充参考；全自动发送仍受发送模式、附件处理错误、产品冲突和风险信号限制

### 开启附件文本读取
1. 确认已安装依赖：`pip install -r requirements.txt`
2. 在 `config.yaml` 中调整 `attachments.max_attachment_bytes` 和 `attachments.max_extracted_chars`
3. PDF、DOCX、XLSX、CSV/TXT 会提取有限文本；损坏或不支持的文件会保留附件记录

### 产品索引
1. 将产品资料放在 `knowledge/结构化数据/products.json` 或按型号放入知识库目录
2. 保持 `config.yaml` 中 `product_index.enabled: true`
3. 系统会用邮件正文、附件文本和图片识别出的型号匹配产品事实

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
