# 火翼邮件 AI Agent

一个面向技术售后场景的本地邮件智能处理系统。系统通过 IMAP 收取邮件，使用大语言模型完成技术分类和风险判断，从本地知识库检索相关资料，并根据运行模式生成待审核草稿或发送低风险技术回复。

项目提供命令行和本机 Web 审核工作台，支持邮件审核、草稿编辑、批准发送、拒绝、知识文件管理以及 Windows 定时任务。

## 功能特性

- 通过 IMAP 异步拉取未读邮件，使用 `Message-ID` 去重，成功处理后才标记为已读。
- 使用 `asyncio` 并发处理多封邮件，可通过 `processing.max_concurrent` 控制并发上限。
- LLM 请求使用 `httpx.AsyncClient` 原生异步调用；标准库 IMAP 和 SMTP 操作移入工作线程，避免阻塞事件循环。
- AI 意图分类、摘要、关键词、情绪、紧急程度和人工介入判断。
- 区分技术问题与订单、报价、物流、退款等业务问题。
- 从本地知识文件中检索型号、中文术语和技术说明。
- 半自动模式下生成草稿，等待人工编辑和批准。
- 全自动模式下仅允许配置中的低风险技术类型直接回复。
- SMTP 发送失败时保留草稿状态，便于稍后重试。
- 提供本机 Web 审核工作台和 CSRF 防护。
- 支持知识文件上传、解析校验、失败回滚、删除和索引重建。
- 支持 Windows 邮件轮询计划任务和 Web 登录自启。
- 内置 BM25、型号精确召回、DashScope 向量召回、RRF 融合和 CPU 本地重排。

## 安全边界

- 默认使用 `semi_auto` 半自动模式，不会直接发送 AI 草稿。
- 业务问题、高风险操作、信息不足和无法确定的问题转人工处理。
- 系统不会自动承诺退款、赔偿、费用或未经确认的处理时效。
- 涉及升级、重置、清除数据、断电拆机或安全风险时应由人工复核。
- Web 服务仅监听 `127.0.0.1:8765`，默认不向局域网或公网开放。
- 邮箱密码和 AI API Key 应存放在 `.env` 或系统环境变量中，不要写入源码或提交到版本库。

## 运行环境

- Python 3.10 或更高版本
- Windows 10/11（Windows 自动任务功能仅支持 Windows）
- 支持 IMAP 和 SMTP 的邮箱账号
- OpenAI、Claude 或 DeepSeek API

## 快速开始

### 1. 安装依赖

建议先创建虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2. 配置敏感信息

复制环境变量模板：

```powershell
Copy-Item .env.example .env
```

编辑 `.env`：

```dotenv
MAIL_ACCOUNT=your-account@example.com
MAIL_PASSWORD=your-client-specific-password
AI_API_KEY=your-ai-api-key
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_API_KEY=your-dashscope-api-key
EMBEDDING_MODEL=text-embedding-v3
```

`MAIL_PASSWORD` 应使用邮箱服务商生成的客户端专用密码，而不是网页登录密码。

系统环境变量的优先级高于 `.env`。也可以在 PowerShell 中临时设置：

```powershell
$env:MAIL_ACCOUNT="your-account@example.com"
$env:MAIL_PASSWORD="your-client-specific-password"
$env:AI_API_KEY="your-ai-api-key"
```

### 3. 调整业务配置

主要配置位于 `config.yaml`：

- `mail`：IMAP/SMTP 地址、端口和轮询间隔。
- `ai`：模型提供商、API 地址、模型名称和生成参数。
- `rag`：检索模式、返回数量和切块参数。
- `workflow`：半自动或全自动模式、草稿目录和自动回复类型。
- `database`：SQLite 数据库路径。
- `processing.max_concurrent`：同时处理的邮件数量，默认值为 `3`。

建议首次运行保持：

```yaml
workflow:
  mode: "semi_auto"
```

当前 RAG 默认使用 Hybrid 检索：

```yaml
rag:
  mode: "hybrid"
  top_k: 5
  chunk_size: 900
  chunk_overlap: 120
  min_confidence: 0.75

processing:
  max_concurrent: 3
```

`hybrid` 已内置 BM25、型号精确召回、DashScope 向量召回、RRF 融合和 CPU 本地重排。Embedding 不可用时自动降级为 BM25，低置信度结果转人工。使用可直接调用百炼 API 的 `EMBEDDING_API_KEY` 后运行 `python main.py rag-build` 构建增量向量索引。

### 4. 检查邮箱连接

```powershell
python main.py test
```

该命令检查 IMAP 连接，不会自动发送测试邮件。

### 5. 处理一次邮件

```powershell
python main.py once
```

不传命令时也默认执行一次：

```powershell
python main.py
```

### 6. 启动 Web 审核工作台

```powershell
python web_app.py
```

浏览器访问：

[http://127.0.0.1:8765](http://127.0.0.1:8765)

Web 工作台支持：

- 查看待审核、转人工、已发送和全部邮件；
- 搜索邮件；
- 查看客户原始邮件和 AI 草稿；
- 编辑、批准或拒绝草稿；
- 删除无效邮件及其草稿；
- 上传和删除知识文件；
- 切换半自动和全自动模式。

## CLI 命令

| 命令 | 说明 |
| --- | --- |
| `python main.py once` | 拉取并处理一次未读邮件 |
| `python main.py forever` | 按配置的间隔持续轮询 |
| `python main.py test` | 测试 IMAP 连接，不发送邮件 |
| `python main.py drafts` | 列出待审核草稿 |
| `python main.py review ID` | 查看指定邮件及草稿 |
| `python main.py edit ID 正文文件` | 使用 UTF-8 文本文件替换草稿正文 |
| `python main.py approve ID` | 批准并发送指定草稿 |
| `python main.py reject ID [原因]` | 拒绝指定草稿 |
| `python main.py stats` | 查看各状态邮件数量 |
| `python main.py rag-build` | 增量构建知识向量索引 |
| `python main.py rag-search 查询` | 调试检索命中、来源和置信度 |
| `python main.py rag-eval` | 计算 Hit@1、Hit@3、MRR 和来源命中率 |
| `python main.py install-task` | 安装 Windows 邮件轮询计划任务 |
| `python main.py remove-task` | 删除 Windows 邮件轮询计划任务 |
| `python main.py install-web-task` | 注册 Web 工作台登录自启并立即启动 |
| `python main.py remove-web-task` | 取消 Web 登录自启并停止对应进程 |

`ID` 是数据库中保存的邮件 `Message-ID`。

## Windows 后台运行

### 邮件轮询计划任务

```powershell
python main.py install-task
```

该命令安装名为 `EmailAIAgentSemiAuto` 的计划任务，每 5 分钟使用 `pythonw.exe` 调用根目录 `main.py once`。任务会明确设置项目根目录为工作目录，适合无控制台后台运行。

每次轮询的执行顺序如下：

1. 异步连接 IMAP 并搜索 `INBOX` 中的 `UNSEEN` 邮件；
2. 拉取并解析全部未读邮件；
3. 按 `processing.max_concurrent` 并发执行意图识别、翻译、知识检索和回复生成；
4. 成功生成草稿、完成发送、转人工或安全跳过后，才将对应邮件标记为已读；
5. 处理异常的邮件保留未读，等待下一轮重试。

查看状态：

```powershell
Get-ScheduledTask -TaskName "EmailAIAgentSemiAuto"
Get-ScheduledTaskInfo -TaskName "EmailAIAgentSemiAuto"
```

`LastTaskResult` 为 `0` 表示最近一次执行成功。若为非零值，请检查 `logs/email_agent.log`，并确认任务 Action 中：

- `Execute` 是不含额外引号的 `pythonw.exe` 完整路径；
- `Arguments` 是 `"项目路径\\main.py" once`；
- `WorkingDirectory` 是项目根目录。

移除任务：

```powershell
python main.py remove-task
```

### Web 工作台登录自启

```powershell
python main.py install-web-task
```

该命令将 `web_app.py` 注册到当前用户的 Windows 登录启动项，并通过 `pythonw.exe` 在后台启动。

移除登录自启并停止服务：

```powershell
python main.py remove-web-task
```

## 知识库

业务知识文件放在根目录 `knowledge/` 中。当前支持：

- `.json`
- `.txt`
- `.md`
- `.docx`
- `.xlsx`

可以直接放入目录，也可以通过 Web 工作台上传。上传和删除完成后会调用统一的 `rebuild()` 重建检索索引。

知识文件建议：

- 使用明确的产品型号和章节标题；
- 一个章节集中描述一个型号或一个技术主题；
- 参数单位、固件版本和兼容条件应写完整；
- 避免将未经确认的销售承诺混入技术资料；
- 更新资料后检查旧版本是否仍会造成知识冲突。

### RAG 接口

领域层定义了与具体检索算法无关的协议：

```python
class KnowledgeRetriever(Protocol):
    def retrieve(
        self,
        query: RetrievalQuery,
        top_k: int = 3,
    ) -> list[KnowledgeHit]: ...

    def rebuild(self) -> IndexStats: ...
```

`KnowledgeHit` 统一包含正文、来源、章节、置信度和 lexical/vector/fusion/rerank 扩展分数。当前默认由 `HybridKnowledgeRetriever` 实现 BM25、型号约束、向量召回和 RRF；Embedding 或重排模型不可用时安全降级，不需要修改邮件服务和 Web 路由。

## 项目结构

```text
email-ai-agent/
├─ main.py                         # CLI 兼容启动器
├─ web_app.py                      # Web 兼容启动器
├─ config.yaml                     # 非敏感业务配置
├─ requirements.txt
├─ src/email_agent/
│  ├─ bootstrap.py                 # 依赖组装
│  ├─ cli.py                       # CLI 命令分发
│  ├─ config.py                    # 配置与环境变量加载
│  ├─ paths.py                     # 项目路径解析
│  ├─ domain/
│  │  ├─ models.py                 # 领域数据模型
│  │  └─ repositories.py           # 检索协议和上下文格式化
│  ├─ application/
│  │  ├─ email_service.py          # 邮件处理编排
│  │  ├─ review_service.py         # 草稿审核服务
│  │  └─ knowledge_service.py      # 知识文件管理服务
│  ├─ infrastructure/
│  │  ├─ database.py               # SQLite
│  │  ├─ mail_fetcher.py           # IMAP
│  │  ├─ mail_sender.py            # SMTP 与草稿文件
│  │  ├─ llm.py                    # AI API
│  │  └─ knowledge/
│  │     ├─ loaders.py             # 文档加载
│  │     ├─ lexical.py             # BM25 与精确检索
│  │     ├─ vector_index.py        # 向量索引
│  │     ├─ hybrid.py              # 混合检索与 RRF
│  │     └─ factory.py             # 检索器工厂
│  └─ web/
│     ├─ app.py                    # Flask 应用工厂
│     ├─ routes/                   # 邮件、知识、设置 Blueprint
│     └─ dist/                     # Vue 构建产物
├─ frontend/                       # Vue 3 + Vite 前端源码
├─ tests/                          # 隔离测试和 Web 兼容测试
├─ knowledge/                      # 本地知识文件
├─ data/                           # SQLite 运行数据，不提交
└─ drafts/                         # 待审核草稿，不提交
```

## 数据与状态

默认运行数据：

- SQLite 数据库：`data/emails.db`
- 草稿目录：`drafts/`
- 知识目录：`knowledge/`

常见邮件状态：

| 状态 | 含义 |
| --- | --- |
| `pending` | 正在处理 |
| `draft_ready` | 草稿已生成，等待审核 |
| `escalated` | 需要人工处理 |
| `replied` | 已发送回复 |
| `rejected` | 人工拒绝发送 |
| `failed` | 处理失败 |
| `skipped_self` | 发件人为自身账号，已跳过 |

SQLite 使用现有 schema 和数据文件，不需要执行破坏性迁移。

## 运行测试

项目测试使用 Python 标准库 `unittest`：

```powershell
python -m unittest discover -s tests -v
```

编译检查：

```powershell
python -m compileall -q src main.py web_app.py tests
```

测试覆盖：

- IMAP/LLM 异步调用、无控制台后台运行和邮件并发上限；
- 关键词、向量、混合检索和来源信息；
- 中文型号和技术术语；
- 知识文件上传、删除和失败回滚；
- 草稿编辑、批准和 SMTP 失败保留；
- 临时 SQLite 数据兼容；
- Web URL、模板和 CSRF 校验。

测试不会连接真实邮箱、发送真实邮件或删除真实知识文件。

## 常见问题

### 提示缺少邮箱账号、密码或 API Key

确认项目根目录存在 `.env`，变量名称与 `.env.example` 一致。不要只修改 `.env.example`。

### IMAP 登录失败

检查：

1. 邮箱是否启用了 IMAP/SMTP；
2. 是否使用完整邮箱地址；
3. 是否使用客户端专用密码；
4. `config.yaml` 中服务器地址和端口是否与邮箱服务商一致。

### 邮箱中存在未读邮件，但任务没有拉取

按以下顺序检查：

1. 手动执行 `python main.py test`，确认 IMAP 登录正常；
2. 执行 `python main.py once`，观察是否能发现 `UNSEEN` 邮件；
3. 查看 `Get-ScheduledTaskInfo -TaskName "EmailAIAgentSemiAuto"` 的 `LastTaskResult`；
4. 查看 `logs/email_agent.log` 是否出现“开始新一轮邮件检查”；
5. 如果任务配置异常，重新执行 `python main.py install-task`。

计划任务成功执行时 `LastTaskResult` 应为 `0`。邮件只有在成功生成草稿、完成发送、转人工或安全跳过后才会标记为已读；失败邮件会保持未读以便重试。

### 邮件生成草稿但没有自动发送

这是 `semi_auto` 模式的正常行为。请进入 Web 工作台审核，或者使用：

```powershell
python main.py approve MESSAGE_ID
```

不建议在未充分验证知识库和安全规则前启用 `full_auto`。

### 知识文件上传后没有匹配结果

检查文件是否包含客户邮件中的产品型号、错误代码或明确技术术语。当前默认使用 Hybrid 检索，融合 BM25、型号精确召回和向量召回；Embedding 不可用时会降级为 BM25。还应检查 `rag.min_confidence`，低于阈值的结果不会作为回复依据。

### 端口 8765 被占用

检查正在运行的 Web 进程：

```powershell
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -like "*web_app.py*" } |
    Select-Object ProcessId, CommandLine
```

可以先执行以下命令停止本项目 Web 服务：

```powershell
python main.py remove-web-task
```

再重新启动或重新安装登录自启。

## 开发说明

- Web 路由应只调用应用服务，不直接操作 SQLite 或知识目录。
- 应用层通过领域协议使用检索功能，避免绑定具体向量数据库。
- 新增知识检索实现时，应返回 `KnowledgeHit` 并支持 `rebuild()`。
- 新功能应使用临时目录、临时 SQLite 和伪造 SMTP 客户端进行测试。
- 不要在自动测试中发送真实邮件、删除真实邮件或修改真实知识文件。

## 许可证

当前仓库未声明开源许可证。未经项目所有者明确授权，不应视为允许复制、分发或商用。
