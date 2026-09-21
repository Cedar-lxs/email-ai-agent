# 可靠邮件处理与防重复发送设计

## 目标

在现有 SQLite 状态库上增加一套可恢复、可审计的邮件处理状态机，解决以下高优先级问题：

- 不再依赖 `UNSEEN` 作为唯一的新邮件发现机制。
- 程序中断后能够继续处理，不遗漏已经扫描但尚未完成的邮件。
- 同一封入站邮件最多创建一个处理任务。
- SMTP 结果不确定时绝不自动重发，避免客户收到重复回复。
- 失败任务能够限次重试、进入死信或人工确认队列，并在管理端可见。
- 保留现有 `processed_emails` 数据及附件、多模态、知识检索、草稿审核流程。

本设计以单机、单邮箱、SQLite 为主要部署形态，同时为以后增加多个邮箱账号保留数据边界。

## 当前风险

当前轮询流程是：查询 `UNSEEN`、并发处理、发送回复、更新 `processed_emails`、最后标记已读。它存在几个故障窗口：

1. IMAP 使用消息序号而不是 UID。邮箱内容变化后，序号不是稳定标识。
2. 只查询未读邮件。人工阅读、邮箱规则或其他客户端可能让邮件在系统处理前消失于扫描范围。
3. 邮件抓取后没有持久任务和原始消息副本。程序在处理中退出时，只能依赖下轮重新抓取。
4. SMTP 已接受邮件但数据库尚未更新时，重启后可能再次发送。
5. SMTP 调用发生异常时，当前布尔返回值无法区分“明确未发送”和“结果未知”。
6. `Message-ID` 适合业务串联，但不能单独代替 IMAP 邮箱位置标识；部分邮件缺失或错误复用该字段。

## 核心原则

系统实现必须满足以下不变量：

- 入站幂等键为 `(account, folder, uid_validity, imap_uid)`，数据库唯一约束负责最终防重。
- `Message-ID` 作为业务关联与辅助排重字段，不作为唯一入站游标。
- UID 游标只有在原始邮件和任务都持久化成功后才能推进。
- 自动投递必须先持久化待发送内容和固定的 RFC `Message-ID`，再调用 SMTP。
- 进入 `sending` 后，不论是进程崩溃还是 SMTP 异常，均不得自动重发。
- 只有能证明发生在 SMTP 投递前的错误才允许自动重试。
- 邮件业务状态、处理任务状态、投递状态分别记录，不能用一个 `status` 同时表达三类事实。
- 所有人工操作都记录操作者、时间、原因和前后状态。

## 总体架构

处理流程拆成四层：

1. **发现与留存**：按 IMAP UID 拉取消息，将原始 `.eml` 原子写入本地暂存目录，并在 SQLite 创建任务。
2. **任务执行**：任务工作器领取可执行任务，复用现有意图分析、多模态、附件、知识库和回复生成流程。
3. **可靠投递**：将最终回复作为投递记录持久化，再通过 SMTP 发送；按明确成功、明确未发送、结果未知分别处理。
4. **运维与人工处置**：管理端展示队列、失败、死信和待确认投递，允许有限且带审计的人工操作。

`processed_emails` 继续作为客户邮件与售后处理结果的业务表。新增表负责队列和投递事实，避免破坏当前列表、详情、历史记录及前端接口。

## SQLite 数据模型

### `mailbox_cursors`

记录每个邮箱文件夹的扫描位置：

| 字段 | 说明 |
| --- | --- |
| `account` | 邮箱账号 |
| `folder` | IMAP 文件夹，首期为 `INBOX` |
| `uid_validity` | 服务端返回的 UIDVALIDITY |
| `last_scanned_uid` | 已可靠留存的最大 UID |
| `updated_at` | 更新时间 |

主键为 `(account, folder)`。

### `mail_jobs`

记录每封入站邮件的执行状态：

| 字段 | 说明 |
| --- | --- |
| `id` | 本地 UUID |
| `account` / `folder` | 邮箱范围 |
| `uid_validity` / `imap_uid` | 稳定 IMAP 标识 |
| `message_id` | RFC Message-ID 或现有哈希回退值 |
| `raw_path` / `raw_sha256` | 原始 `.eml` 文件和校验值 |
| `status` | 当前任务状态 |
| `attempt_count` | 处理尝试次数 |
| `next_attempt_at` | 下次允许领取时间 |
| `lease_owner` / `lease_expires_at` | 工作器租约 |
| `last_error_code` / `last_error` | 最近一次失败摘要 |
| `created_at` / `updated_at` / `finished_at` | 生命周期时间 |

唯一约束为 `(account, folder, uid_validity, imap_uid)`；`message_id` 建普通索引，用于辅助诊断与跨 UIDVALIDITY 变更时的保守排重。

### `outbound_deliveries`

记录每一次实际投递尝试：

| 字段 | 说明 |
| --- | --- |
| `id` | 投递 UUID |
| `job_id` | 对应入站任务 |
| `email_message_id` | 发送前生成并固定的 RFC Message-ID |
| `recipient` / `subject` | 投递目标与主题 |
| `body` / `body_sha256` | 已审核或自动生成的最终正文及校验值 |
| `status` | `prepared`、`sending`、`accepted`、`uncertain`、`failed_safe`、`cancelled` |
| `smtp_response` | 可安全记录的 SMTP 响应摘要 |
| `started_at` / `completed_at` | 投递时间 |
| `created_by` | `auto` 或人工账号 |

同一任务首个自动回复只允许一条活动投递记录。人工确认需要重新发送时，不覆盖原记录，而是新建投递记录并关联原记录。

### `mail_job_events`

追加式审计日志：`job_id`、事件类型、原状态、新状态、操作者、原因、结构化元数据和时间。正文、密码、令牌和完整附件内容不写入事件元数据。

## 原始邮件留存

原始邮件写入 `data/inbox_spool/YYYY-MM/`，文件名使用任务 UUID，不使用客户提供的文件名。流程为：

1. 通过 UID 执行 `BODY.PEEK[]`，不改变已读状态。
2. 将字节写入同目录临时文件，刷新并原子重命名为 `.eml`。
3. 计算 SHA-256。
4. 在一个 SQLite 事务中插入 `mail_jobs` 并推进 `mailbox_cursors`。
5. 唯一约束冲突视为已经发现，不重复创建任务。

这样即使后续 IMAP 暂时不可用，任务仍可从本地原始邮件恢复解析。暂存文件只允许由受控路径读取，默认在任务进入终态后保留一段可配置的审计期限，再由独立清理任务删除。

如果进程在文件原子重命名后、SQLite 事务提交前退出，可能留下没有任务引用的孤立 `.eml`。启动恢复和定期清理会比对数据库引用，只删除超过安全等待期的孤立文件；游标不会因该文件存在而推进，所以下轮仍会重新抓取该 UID。

## IMAP 扫描

- `SELECT INBOX` 后读取 `UIDVALIDITY`。
- 使用 `UID SEARCH UID <last_scanned_uid + 1>:*`，不以已读标记筛选。
- 每批按 UID 升序抓取，单封邮件完成可靠留存后才推进游标。
- 标记已读仅是用户体验动作，不承担队列确认职责。
- 当 `UIDVALIDITY` 改变时，记录审计事件并从新 UID 空间重新扫描；利用新唯一键、原始哈希和 `Message-ID` 做保守排重。无法确认是否相同的邮件可重新分析，但不得由此自动产生第二次投递。
- 服务端单封抓取失败不越过该 UID 推进游标，避免永久遗漏；批次停止并在下轮重试。

## 任务状态机

主要状态如下：

```text
pending -> processing -> draft_ready
                      -> escalated
                      -> send_prepared -> sending -> sent
                      -> retry_wait -> processing
                      -> dead_letter

sending -> awaiting_confirmation
```

- `pending`：已可靠留存，等待处理。
- `processing`：工作器持有有效租约。
- `draft_ready`、`escalated`、`sent`：正常终态。
- `retry_wait`：明确可重试的处理错误，按退避时间等待。
- `dead_letter`：超过最大处理次数，等待人工处置。
- `send_prepared`：回复内容和投递 ID 已持久化，尚未进入 SMTP。
- `sending`：已经开始不可安全回滚的 SMTP 投递阶段。
- `awaiting_confirmation`：发送结果未知，禁止自动重试。

任务领取使用短事务和 `BEGIN IMMEDIATE`。工作器写入租约后再执行耗时操作。过期的 `processing` 可恢复到 `retry_wait`；过期的 `sending` 必须恢复为 `awaiting_confirmation`。

## 重试策略

自动重试只覆盖以下情况：

- 邮件尚未进入 SMTP 投递阶段时的临时网络错误。
- AI、多模态、知识检索等外部服务的瞬时错误。
- SMTP 建连或认证阶段、且能确认尚未提交邮件内容的临时错误。

默认建议：最多 3 次处理尝试，退避 1 分钟、5 分钟、30 分钟。超过上限进入 `dead_letter`。业务判定需要人工、证据不足或高风险不属于失败，不重试，直接进入 `escalated`。

## 防重复投递

发送前执行以下顺序：

1. 生成投递 UUID 及固定的 `Message-ID`，例如 `<ea-{delivery_uuid}@configured-domain>`。
2. 将收件人、主题、最终正文、正文哈希和 `prepared` 状态写入 SQLite。
3. 在事务中将投递和任务改为 `sending` 并提交。
4. 构造邮件时复用已保存的 `Message-ID`，调用 SMTP。
5. SMTP 明确返回成功后，将投递改为 `accepted`，任务和 `processed_emails` 改为已回复。

如果进程在第 3 步之后退出，或 `send_message` 阶段抛出超时、断线等无法证明未提交的异常，投递改为 `uncertain`，任务改为 `awaiting_confirmation`。系统不自动重发。

人工可以：

- 标记为“已确认发送”，使任务结束；
- 标记为“确认未发送”，然后显式授权重新发送；
- 取消回复并转人工。

显式重发会创建新的投递记录和新的 RFC `Message-ID`，并记录操作者与原因。前端不能提供无审计的一键循环重试。

## 与现有业务流程的衔接

- 现有意图、多模态识别、设备标签字段提取、知识库/博查匹配和风险判断逻辑保持不变，由任务工作器调用。
- 首次开始业务处理时再创建或补齐 `processed_emails`，原有 `message_id` 主键继续服务前端和对话历史。
- 自动发送路径改为先创建 `outbound_deliveries`，不再直接调用返回布尔值的 `send_reply`。
- 人工批准草稿也复用同一可靠投递服务，因此同样具备防重和待确认状态。
- `processed_emails.status` 继续表示业务结果；队列内部状态由 `mail_jobs.status` 表示。
- 现有历史邮件不回填任务，避免把 110 封旧记录误当成待处理任务。迁移只增加表、索引和配置。

## 管理端与 API

管理端增加“处理异常”入口，默认只在存在异常时显示计数。首期需要支持：

- 查看 `retry_wait`、`dead_letter`、`awaiting_confirmation` 任务。
- 查看任务时间线、尝试次数和脱敏错误摘要。
- 对死信执行“重新分析”或“转人工”。
- 对待确认投递执行“确认已发送”“确认未发送并授权重发”“转人工”。

建议接口：

- `GET /api/operations/mail-jobs?status=...`
- `GET /api/operations/mail-jobs/<id>`
- `POST /api/operations/mail-jobs/<id>/retry`
- `POST /api/operations/deliveries/<id>/confirm-sent`
- `POST /api/operations/deliveries/<id>/authorize-resend`
- `POST /api/operations/mail-jobs/<id>/escalate`

所有写操作沿用现有登录、CSRF 和审计约束，并在服务端再次校验允许的状态转换。

## 配置

新增建议配置：

```yaml
processing:
  max_concurrent: 3
  batch_size: 20
  max_attempts: 3
  retry_delays_seconds: [60, 300, 1800]
  lease_seconds: 600
  smtp_timeout: 30
  spool_retention_days: 30
```

SQLite 延续 WAL 和 `busy_timeout`。首期仍只运行一个调度进程，但数据库租约和唯一约束可防止计划任务重叠运行造成重复领取。

## 安全与隐私

- `.eml` 可能包含客户隐私和附件，目录不通过静态服务器暴露。
- 原始邮件下载必须经过已认证、按任务授权的受控接口；首期管理端无需提供整封下载。
- 文件路径使用服务端生成值，并验证解析后的路径位于 spool 根目录。
- 错误日志和事件不记录邮箱密码、API 密钥、完整邮件正文或附件二进制。
- 清理任务只删除已经过保留期且处于终态的 spool 文件；数据库审计记录保留。

## 可观测性

日志统一带上 `job_id`、入站 UID、`message_id` 和 `delivery_id`，但不输出正文。统计至少包括：

- 最近扫描 UID 与最后成功扫描时间。
- 各任务状态数量及最老等待时间。
- 处理成功率、重试次数、死信数量。
- 待确认投递数量及最老等待时间。
- SMTP 明确成功、明确未发送、结果未知的数量。

待确认投递和死信属于需要人工关注的告警条件。

## 测试策略

### 单元测试

- UID 扫描与游标推进。
- 唯一键冲突不创建第二个任务。
- 原始邮件原子落盘及哈希校验。
- UIDVALIDITY 变化后的保守处理。
- 合法和非法状态转换。
- 租约领取、续期和过期恢复。
- 退避时间、最大尝试次数和死信转换。
- 固定 RFC `Message-ID` 的生成和复用。
- SMTP 前明确失败可重试，进入发送阶段后的异常转待确认。

### 故障注入测试

在以下位置模拟进程退出或异常：

- 原始邮件写入前、写入后、任务事务提交前后。
- 任务领取后、业务邮件落库前后。
- 投递记录创建前后、切换到 `sending` 前后。
- SMTP 已返回成功但本地成功事务尚未提交。

每个测试必须验证：不遗漏入站任务、不产生第二个自动投递、状态可由重启恢复。

### 集成与回归测试

- 使用伪 IMAP 服务验证 UID、已读标记和 UIDVALIDITY。
- 使用可控 SMTP 服务验证明确成功、认证失败、DATA 阶段断线和超时。
- 验证人工草稿批准也经过可靠投递服务。
- 验证异常列表 API 权限、CSRF、状态校验和审计事件。
- 保持现有 120 项测试通过，并补充数据库升级兼容测试。

## 发布步骤

1. 只部署新增表、spool 和 UID 发现逻辑，保持 `semi_auto`，观察任务创建与重复率。
2. 接入任务工作器和草稿流程，确认租约恢复、附件和多模态处理正常。
3. 接入人工批准的可靠投递，演练待确认处置。
4. 最后接入低风险自动发送，先小范围启用并监控待确认与死信数量。

任一阶段都可以回到 `semi_auto`；已有业务邮件表不回滚、不删除。

## 不在本期范围

- 多机器分布式队列或外部消息中间件。
- 邮件退信、送达回执和客户是否真正阅读的追踪。
- 自动判断 SMTP 结果未知的邮件一定送达或一定未送达。
- 历史 110 封邮件的任务回填。
- 多文件夹规则和多个邮箱账号的管理界面。

## 验收标准

- 同一 IMAP UID 被扫描多次时，只存在一个任务。
- 人工把未处理邮件标为已读后，系统仍能通过 UID 游标发现它。
- 进程在任意业务处理步骤退出后，任务可自动恢复或进入明确的人工队列。
- 进程在 `sending` 后退出时，重启不会自动重发。
- 自动重试达到上限后进入死信，错误原因和时间线可在管理端查看。
- 自动发送与人工批准共用同一投递账本和防重规则。
- 升级后现有历史邮件、附件、多模态结果、知识库和登录数据保持可用。
- 完整测试集通过，并新增覆盖关键故障窗口的测试。
