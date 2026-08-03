"""邮件 Agent：收件、技术分类、知识检索、草稿审核与发送。"""
import asyncio
import subprocess
import sys
import time
from pathlib import Path

from email_agent.config import load_config
from email_agent.domain.models import RetrievalQuery
from email_agent.domain.repositories import KnowledgeContextFormatter
from email_agent.infrastructure.database import EmailDB
from email_agent.infrastructure.knowledge.factory import create_retriever
from email_agent.infrastructure.llm import AIProcessor
from email_agent.infrastructure.mail_fetcher import MailFetcher
from email_agent.infrastructure.mail_sender import MailSender
from email_agent.paths import get_project_paths, resolve_from_root
from email_agent.application.review_service import ReviewService
from email_agent.logger import setup_logger, get_logger


def _safe_print(*args, **kwargs):
    """在 pythonw 等无控制台环境中静默跳过终端输出。"""
    if sys.stdout is not None:
        print(*args, **kwargs)


class EmailAgent:
    def __init__(self, config_path: str = None):
        self.config = load_config(config_path)
        paths = get_project_paths()
        
        # 设置日志
        log_dir = self.config.get("logging", {}).get("dir", str(paths.root / "logs"))
        log_level = self.config.get("logging", {}).get("level", "INFO")
        setup_logger("email_agent", log_dir, log_level)
        self.logger = get_logger("email_agent")
        self.logger.info("=" * 60)
        self.logger.info("EmailAgent 初始化开始")
        
        db_path = resolve_from_root(self.config["database"]["path"], paths.root)
        draft_path = resolve_from_root(self.config["workflow"]["draft_dir"], paths.root)
        self.db = EmailDB(str(db_path))
        mail = self.config["mail"]
        self.sender = MailSender(
            mail["smtp_server"], mail["smtp_port"], mail["account"], mail["password"],
            self.config["ai"].get("company", "Technical Support"),
        )
        self.ai = AIProcessor(self.config["ai"])
        self.kb = create_retriever(self.config, paths.knowledge)
        self.retriever = self.kb
        self.top_k = int(self.config.get("rag", {}).get("top_k", 3))
        self.mode = self.db.get_setting(
            "workflow_mode", self.config["workflow"]["mode"]
        )
        self.draft_dir = str(draft_path)
        self.review = ReviewService(self.db, self.sender, draft_path)
        
        # 并发处理配置
        self.max_concurrent = int(self.config.get("processing", {}).get("max_concurrent", 3))
        self.logger.info(f"EmailAgent 初始化完成: mode={self.mode}, max_concurrent={self.max_concurrent}")
        self.logger.info("=" * 60)

    def _fetcher(self):
        mail = self.config["mail"]
        return MailFetcher(mail["imap_server"], mail["imap_port"],
                           mail["account"], mail["password"])

    def run_once(self):
        """同步兼容入口；实际轮询与处理运行在同一个事件循环中。"""
        return asyncio.run(self.run_once_async())

    async def run_once_async(self):
        _safe_print(f"\n{'=' * 50}\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] 开始检查新邮件")
        self.logger.info("开始新一轮邮件检查")
        fetcher = self._fetcher()
        try:
            await fetcher.connect_async()
            emails = await fetcher.fetch_unread_async()
            if not emails:
                _safe_print("没有未读邮件")
                self.logger.info("没有未读邮件")
                return []
            _safe_print(f"发现 {len(emails)} 封未读邮件")
            self.logger.info(f"发现 {len(emails)} 封未读邮件，开始并发处理")

            results = await self._process_emails_concurrently(emails)

            for message, completed in zip(emails, results):
                if completed:
                    await fetcher.mark_seen_async(message.imap_uid)
                    self.logger.info(f"邮件 {message.message_id} 处理完成，已标记为已读")
                else:
                    self.logger.warning(f"邮件 {message.message_id} 处理失败，保留未读状态")
            return results
        except Exception as exc:
            _safe_print(f"邮箱连接失败: {exc}")
            self.logger.error(f"邮箱连接失败: {exc}", exc_info=True)
            return []
        finally:
            await fetcher.disconnect_async()
    
    async def _process_emails_concurrently(self, emails):
        """并发处理多封邮件"""
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        async def process_with_limit(email, index, total):
            async with semaphore:
                _safe_print(f"\n[{index}/{total}] {email.subject[:60]} | {email.sender}")
                self.logger.info(f"开始处理邮件 [{index}/{total}]: {email.message_id}")
                try:
                    completed = await self._process_email_async(email)
                    self.logger.info(f"邮件 {email.message_id} 处理{'成功' if completed else '失败'}")
                    return completed
                except Exception as exc:
                    self._record_failure(email, exc)
                    _safe_print(f"处理失败，将保留未读以便重试: {exc}")
                    self.logger.error(f"邮件 {email.message_id} 处理异常: {exc}", exc_info=True)
                    return False
        
        tasks = [process_with_limit(email, i+1, len(emails)) 
                 for i, email in enumerate(emails)]
        return await asyncio.gather(*tasks, return_exceptions=False)

    async def _process_email_async(self, email) -> bool:
        """异步处理单封邮件"""
        if email.sender.lower() == self.config["mail"]["account"].lower():
            self.db.mark_processed(
                email.message_id, email.subject, email.sender, status="skipped_self",
                original_body=email.body_text, in_reply_to=email.in_reply_to,
            )
            _safe_print("售后邮箱自身邮件，已跳过以防回复循环")
            self.logger.info(f"跳过自身邮件: {email.message_id}")
            return True
        if self.db.is_processed(email.message_id):
            _safe_print("已处理过，跳过")
            self.logger.info(f"邮件已处理过: {email.message_id}")
            return True

        # 异步意图分析
        intent = await self._retry_async(
            lambda: self.ai.analyze_intent_async(email.subject, email.body_text), 
            "AI 意图分析"
        )
        _safe_print(f"意图: {intent.intent} | 情绪: {intent.sentiment} | 紧急: {intent.urgency}")
        self.logger.info(f"意图分析完成: intent={intent.intent}, sentiment={intent.sentiment}, "
                        f"urgency={intent.urgency}, needs_human={intent.needs_human}")
        
        self.db.mark_processed(
            email.message_id, email.subject, email.sender, intent.intent, intent.sentiment,
            "pending", email.body_text, email.in_reply_to,
        )
        self.db.save_conversation(
            email.sender, email.message_id, "customer",
            f"[{email.subject}]\n{email.body_text[:2000]}",
        )

        if self._needs_human(intent):
            self.db.update_status(email.message_id, "escalated", notes="业务或高风险问题")
            _safe_print("已转人工处理")
            self.logger.info(f"邮件 {email.message_id} 已转人工: 业务或高风险问题")
            return True

        # 异步翻译
        try:
            translated = await self._retry_async(
                lambda: self.ai.translate_for_retrieval_async(email.subject, email.body_text),
                "客户邮件中文翻译",
            )
        except Exception as exc:
            self.db.update_status(
                email.message_id, "escalated", notes=f"未生成有效中文检索文本，已转人工：{exc}"
            )
            _safe_print(f"客户邮件未能转换为中文检索文本，已转人工处理: {exc}")
            self.logger.warning(f"邮件 {email.message_id} 翻译失败，已转人工: {exc}")
            return True

        retrieval_query = RetrievalQuery(
            text=translated["body"],
            subject=translated["subject"],
            summary="",
            intent=intent.intent,
            keywords=tuple(translated["keywords"]),
            identifiers=tuple(self.kb.store._identifiers(
                f"{email.subject}\n{email.body_text}\n{translated['subject']}\n"
                f"{translated['body']}\n{' '.join(translated['keywords'])}"
            )),
        )
        hits = self.retriever.retrieve(retrieval_query, self.top_k)
        evidence_threshold = float(self.config.get("rag", {}).get("min_confidence", 0.75))
        hits = [hit for hit in hits if hit.score >= evidence_threshold]
        self.logger.info(f"知识检索完成: 找到 {len(hits)} 条符合阈值的知识")
        
        self.db.save_retrieval_trace(
            email.message_id, getattr(self.retriever, "last_trace", {
                "mode": "lexical", "query": retrieval_query.combined_text,
                "hits": [{"source": hit.source, "section": hit.section,
                          "score": hit.score} for hit in hits],
            })
        )
        knowledge = KnowledgeContextFormatter.format(hits)
        if not hits:
            self.db.update_status(
                email.message_id, "escalated",
                notes=f"没有置信度达到 {evidence_threshold:.0%} 的直接知识依据，已转人工"
            )
            _safe_print(f"没有置信度达到 {evidence_threshold:.0%} 的知识依据，已转人工处理")
            self.logger.info(f"邮件 {email.message_id} 无足够知识依据，已转人工")
            return True
        
        history = "\n".join(
            f"[{row['role']}]: {row['content'][:500]}"
            for row in reversed(self.db.get_history_for_sender(email.sender, 3))
        )
        
        # 异步生成回复
        try:
            reply = await self._retry_async(
                lambda: self.ai.generate_reply_async(
                    email.subject, email.body_text, intent, knowledge, history
                ), "AI 回复生成"
            )
        except Exception as exc:
            self.db.update_status(
                email.message_id, "escalated", notes=f"AI 未生成有效回复，已转人工：{exc}"
            )
            _safe_print(f"AI 未生成有效回复，已转人工处理: {exc}")
            self.logger.warning(f"邮件 {email.message_id} 回复生成失败，已转人工: {exc}")
            return True

        allowed = intent.intent in self.config["workflow"]["auto_reply_types"]
        if self.mode == "full_auto" and allowed:
            sent = await asyncio.to_thread(
                self.sender.send_reply,
                email.sender, email.subject, reply, email.message_id,
            )
            if not sent:
                raise RuntimeError("SMTP 自动回复失败")
            self.db.update_status(email.message_id, "replied", reply)
            self.db.save_conversation(email.sender, email.message_id, "agent", reply)
            _safe_print(f"自动回复已发送，主题: {self.sender.build_reply_subject(email.subject)}")
            self.logger.info(f"邮件 {email.message_id} 自动回复已发送")
            return True

        path = self.sender.save_draft(
            email.sender, email.subject, reply, self.draft_dir,
            message_id=email.message_id,
        )
        self.db.update_status(email.message_id, "draft_ready", reply, path)
        self.db.save_conversation(email.sender, email.message_id, "agent", reply)
        _safe_print(f"待审核草稿已保存: {path}")
        self.logger.info(f"邮件 {email.message_id} 草稿已生成: {path}")
        return True
    
    def _process_email(self, email) -> bool:
        """同步处理单封邮件（保留向后兼容）"""
        self.logger.info(f"开始同步处理邮件: {email.message_id}")
        if email.sender.lower() == self.config["mail"]["account"].lower():
            self.db.mark_processed(
                email.message_id, email.subject, email.sender, status="skipped_self",
                original_body=email.body_text, in_reply_to=email.in_reply_to,
            )
            _safe_print("售后邮箱自身邮件，已跳过以防回复循环")
            self.logger.info(f"跳过自身邮件: {email.message_id}")
            return True
        if self.db.is_processed(email.message_id):
            _safe_print("已处理过，跳过")
            self.logger.info(f"邮件已处理过: {email.message_id}")
            return True

        intent = self._retry(
            lambda: self.ai.analyze_intent(email.subject, email.body_text), "AI 意图分析"
        )
        _safe_print(f"意图: {intent.intent} | 情绪: {intent.sentiment} | 紧急: {intent.urgency}")
        self.logger.info(f"意图分析完成: intent={intent.intent}, sentiment={intent.sentiment}")
        
        self.db.mark_processed(
            email.message_id, email.subject, email.sender, intent.intent, intent.sentiment,
            "pending", email.body_text, email.in_reply_to,
        )
        self.db.save_conversation(
            email.sender, email.message_id, "customer",
            f"[{email.subject}]\n{email.body_text[:2000]}",
        )

        if self._needs_human(intent):
            self.db.update_status(email.message_id, "escalated", notes="业务或高风险问题")
            _safe_print("已转人工处理")
            self.logger.info(f"邮件 {email.message_id} 已转人工")
            return True

        try:
            translated = self._retry(
                lambda: self.ai.translate_for_retrieval(email.subject, email.body_text),
                "客户邮件中文翻译",
            )
        except Exception as exc:
            self.db.update_status(
                email.message_id, "escalated", notes=f"未生成有效中文检索文本，已转人工：{exc}"
            )
            _safe_print(f"客户邮件未能转换为中文检索文本，已转人工处理: {exc}")
            self.logger.warning(f"邮件 {email.message_id} 翻译失败，已转人工: {exc}")
            return True

        retrieval_query = RetrievalQuery(
            text=translated["body"],
            subject=translated["subject"],
            summary="",
            intent=intent.intent,
            keywords=tuple(translated["keywords"]),
            identifiers=tuple(self.kb.store._identifiers(
                f"{email.subject}\n{email.body_text}\n{translated['subject']}\n"
                f"{translated['body']}\n{' '.join(translated['keywords'])}"
            )),
        )
        hits = self.retriever.retrieve(retrieval_query, self.top_k)
        evidence_threshold = float(self.config.get("rag", {}).get("min_confidence", 0.75))
        hits = [hit for hit in hits if hit.score >= evidence_threshold]
        self.logger.info(f"知识检索完成: 找到 {len(hits)} 条符合阈值的知识")
        
        self.db.save_retrieval_trace(
            email.message_id, getattr(self.retriever, "last_trace", {
                "mode": "lexical", "query": retrieval_query.combined_text,
                "hits": [{"source": hit.source, "section": hit.section,
                          "score": hit.score} for hit in hits],
            })
        )
        knowledge = KnowledgeContextFormatter.format(hits)
        if not hits:
            self.db.update_status(
                email.message_id, "escalated",
                notes=f"没有置信度达到 {evidence_threshold:.0%} 的直接知识依据，已转人工"
            )
            _safe_print(f"没有置信度达到 {evidence_threshold:.0%} 的知识依据，已转人工处理")
            self.logger.info(f"邮件 {email.message_id} 无足够知识依据，已转人工")
            return True
        history = "\n".join(
            f"[{row['role']}]: {row['content'][:500]}"
            for row in reversed(self.db.get_history_for_sender(email.sender, 3))
        )
        try:
            reply = self._retry(
                lambda: self.ai.generate_reply(
                    email.subject, email.body_text, intent, knowledge, history
                ), "AI 回复生成"
            )
        except Exception as exc:
            self.db.update_status(
                email.message_id, "escalated", notes=f"AI 未生成有效回复，已转人工：{exc}"
            )
            _safe_print(f"AI 未生成有效回复，已转人工处理: {exc}")
            self.logger.warning(f"邮件 {email.message_id} 回复生成失败，已转人工: {exc}")
            return True

        allowed = intent.intent in self.config["workflow"]["auto_reply_types"]
        if self.mode == "full_auto" and allowed:
            if not self.sender.send_reply(
                email.sender, email.subject, reply, email.message_id
            ):
                raise RuntimeError("SMTP 自动回复失败")
            self.db.update_status(email.message_id, "replied", reply)
            self.db.save_conversation(email.sender, email.message_id, "agent", reply)
            _safe_print(f"自动回复已发送，主题: {self.sender.build_reply_subject(email.subject)}")
            self.logger.info(f"邮件 {email.message_id} 自动回复已发送")
            return True

        path = self.sender.save_draft(
            email.sender, email.subject, reply, self.draft_dir,
            message_id=email.message_id,
        )
        self.db.update_status(email.message_id, "draft_ready", reply, path)
        self.db.save_conversation(email.sender, email.message_id, "agent", reply)
        _safe_print(f"待审核草稿已保存: {path}")
        self.logger.info(f"邮件 {email.message_id} 草稿已生成: {path}")
        return True

    def _record_failure(self, email, exc):
        if not self.db.get_email(email.message_id):
            self.db.mark_processed(
                email.message_id, email.subject, email.sender, status="failed",
                original_body=email.body_text, in_reply_to=email.in_reply_to,
            )
        self.db.mark_failed(email.message_id, str(exc))

    @staticmethod
    def _retry(operation, name: str, attempts: int = 3):
        last_error = None
        logger = get_logger("email_agent")
        for attempt in range(1, attempts + 1):
            try:
                return operation()
            except Exception as exc:
                last_error = exc
                if attempt < attempts:
                    delay = 2 ** (attempt - 1)
                    _safe_print(f"{name}失败，第 {attempt} 次重试将在 {delay} 秒后进行")
                    logger.warning(f"{name}失败，第 {attempt} 次重试将在 {delay} 秒后进行: {exc}")
                    time.sleep(delay)
        logger.error(f"{name}连续失败: {last_error}")
        raise RuntimeError(f"{name}连续失败: {last_error}") from last_error
    
    @staticmethod
    async def _retry_async(operation, name: str, attempts: int = 3):
        """异步重试装饰器"""
        last_error = None
        logger = get_logger("email_agent")
        for attempt in range(1, attempts + 1):
            try:
                return await operation()
            except Exception as exc:
                last_error = exc
                if attempt < attempts:
                    delay = 2 ** (attempt - 1)
                    _safe_print(f"{name}失败，第 {attempt} 次重试将在 {delay} 秒后进行")
                    logger.warning(f"{name}失败，第 {attempt} 次重试将在 {delay} 秒后进行: {exc}")
                    await asyncio.sleep(delay)
        logger.error(f"{name}连续失败: {last_error}")
        raise RuntimeError(f"{name}连续失败: {last_error}") from last_error

    def _needs_human(self, intent) -> bool:
        return (
            intent.intent in self.config["workflow"]["always_human_types"]
            or intent.needs_human
            or intent.sentiment == "angry"
        )

    def list_drafts(self):
        rows = self.db.get_pending_drafts()
        if not rows:
            _safe_print("没有待审核草稿")
            return
        _safe_print(f"待审核草稿: {len(rows)}")
        for row in rows:
            _safe_print(f"{row['message_id']} | {row['sender']} | {row['subject']} | {row['draft_path']}")

    def review_draft(self, message_id: str):
        row = self._require_draft(message_id)
        _safe_print(f"ID: {row['message_id']}\n发件人: {row['sender']}\n原主题: {row['subject']}")
        _safe_print(f"回复主题: {self.sender.build_reply_subject(row['subject'])}")
        _safe_print(f"状态: {row['status']}\n草稿文件: {row['draft_path']}\n")
        _safe_print("客户原文:\n" + (row["original_body"] or "(无正文)"))
        _safe_print("\n回复草稿:\n" + self._draft_body(row))

    def edit_draft(self, message_id: str, body_file: str):
        path = self.review.edit(message_id, Path(body_file).read_text(encoding="utf-8"))
        _safe_print(f"草稿已更新: {path}")

    def approve_draft(self, message_id: str):
        row = self.review.require_draft(message_id)
        self.review.approve(message_id)
        _safe_print(f"发送成功，主题: {self.sender.build_reply_subject(row['subject'])}")

    def reject_draft(self, message_id: str, reason: str = "人工拒绝"):
        self.review.reject(message_id, reason)
        _safe_print("草稿已拒绝，不会发送")

    def show_stats(self):
        rows = self.db.get_stats()
        if not rows:
            _safe_print("暂无处理记录")
        for row in rows:
            _safe_print(f"{row['status']}: {row['count']}")

    def _require_draft(self, message_id: str):
        return self.review.require_draft(message_id)

    def _draft_body(self, row) -> str:
        return self.review.draft_body(row)

    def install_task(self):
        """安装每 5 分钟执行一次的 Windows 计划任务。"""
        if sys.platform != "win32":
            raise RuntimeError("自动安装目前仅支持 Windows")
        python = Path(sys.executable).resolve()
        pythonw = python.with_name("pythonw.exe")
        if not pythonw.is_file():
            raise RuntimeError(f"找不到后台解释器: {pythonw}")
        script = get_project_paths().root / "main.py"
        task_command = f'"{pythonw}" "{script}" once'
        result = subprocess.run(
            ["schtasks", "/Create", "/TN", "EmailAIAgentSemiAuto", "/TR", task_command,
             "/SC", "MINUTE", "/MO", "5", "/F"],
            capture_output=True, text=True, encoding="gbk", errors="replace",
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        escaped_pythonw = str(pythonw).replace("'", "''")
        escaped_script = str(script).replace("'", "''")
        escaped_workdir = str(script.parent).replace("'", "''")
        settings_command = (
            "$action=New-ScheduledTaskAction "
            f"-Execute '{escaped_pythonw}' "
            f"-Argument '\"{escaped_script}\" once' "
            f"-WorkingDirectory '{escaped_workdir}';"
            "Set-ScheduledTask -TaskName 'EmailAIAgentSemiAuto' -Action $action | Out-Null;"
            "$task=Get-ScheduledTask -TaskName 'EmailAIAgentSemiAuto';"
            "$task.Settings.DisallowStartIfOnBatteries=$false;"
            "$task.Settings.StopIfGoingOnBatteries=$false;"
            "$task.Settings.StartWhenAvailable=$true;"
            "$task.Settings.Hidden=$true;"
            "$task.Settings.MultipleInstances='IgnoreNew';"
            "Set-ScheduledTask -InputObject $task | Out-Null"
        )
        settings_result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", settings_command],
            capture_output=True, text=True, encoding="gbk", errors="replace",
        )
        if settings_result.returncode != 0:
            raise RuntimeError(settings_result.stderr.strip() or settings_result.stdout.strip())
        _safe_print("计划任务已安装：EmailAIAgentSemiAuto，每 5 分钟无窗口后台生成待审核草稿")

    def remove_task(self):
        if sys.platform != "win32":
            raise RuntimeError("自动移除目前仅支持 Windows")
        result = subprocess.run(
            ["schtasks", "/Delete", "/TN", "EmailAIAgentSemiAuto", "/F"],
            capture_output=True, text=True, encoding="gbk", errors="replace",
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        _safe_print("计划任务已移除")

    def install_web_task(self):
        """注册当前用户登录启动项，并立即无窗口启动本机 Web 审核界面。"""
        if sys.platform != "win32":
            raise RuntimeError("自动安装目前仅支持 Windows")
        pythonw = Path(sys.executable).resolve().with_name("pythonw.exe")
        web_script = get_project_paths().root / "web_app.py"
        if not pythonw.is_file():
            raise RuntimeError(f"找不到后台解释器: {pythonw}")
        task_command = f'"{pythonw}" "{web_script}"'
        result = subprocess.run(
            ["reg", "add", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
             "/v", "EmailAIAgentReviewWeb", "/t", "REG_SZ", "/d", task_command, "/f"],
            capture_output=True, text=True, encoding="gbk", errors="replace",
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        creation_flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        subprocess.Popen(
            [str(pythonw), str(web_script)], cwd=str(web_script.parent),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=creation_flags, close_fds=True,
        )
        _safe_print("Web 审核后台已注册为登录自启并启动：http://127.0.0.1:8765")

    def remove_web_task(self):
        if sys.platform != "win32":
            raise RuntimeError("自动移除目前仅支持 Windows")
        result = subprocess.run(
            ["reg", "delete", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
             "/v", "EmailAIAgentReviewWeb", "/f"],
            capture_output=True, text=True, encoding="gbk", errors="replace",
        )
        if result.returncode not in {0, 1}:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        command = (
            "$target='" + str(get_project_paths().root / "web_app.py").replace("'", "''") + "';"
            "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | "
            "Where-Object {$_.CommandLine -like ('*'+$target+'*')} | "
            "ForEach-Object {Stop-Process -Id $_.ProcessId -Force}"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True)
        _safe_print("Web 审核后台已停止并取消登录自启")

    def run_forever(self):
        """同步兼容入口。"""
        try:
            asyncio.run(self.run_forever_async())
        except KeyboardInterrupt:
            _safe_print("已停止")

    async def run_forever_async(self):
        interval = self.config["mail"]["poll_interval"]
        _safe_print(f"邮件 Agent 已启动，模式: {self.mode}，间隔: {interval} 秒")
        while True:
            await self.run_once_async()
            await asyncio.sleep(interval)

    def test_connection(self):
        asyncio.run(self.test_connection_async())

    async def test_connection_async(self):
        fetcher = self._fetcher()
        try:
            await fetcher.connect_async()
            _safe_print("IMAP 连接成功")
        finally:
            await fetcher.disconnect_async()
        _safe_print("SMTP 测试请使用显式发信测试，连接测试不再自动发送邮件")


def usage():
    _safe_print("用法: python main.py [once|forever|test|drafts|review ID|edit ID 正文文件|approve ID|reject ID [原因]|stats|rag-build|rag-search 查询|rag-eval|install-task|remove-task|install-web-task|remove-web-task]")
