import asyncio
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email_agent.application.email_service import EmailAgent
from email_agent.application.knowledge_service import KnowledgeService
from email_agent.infrastructure.knowledge.lexical import LexicalKnowledgeRetriever
from email_agent.infrastructure.mail_fetcher import MailFetcher


class Upload:
    def __init__(self, name, content):
        self.filename = name
        self.content = content

    def save(self, target):
        Path(target).write_bytes(self.content)


class KnowledgeServiceTests(unittest.TestCase):
    @staticmethod
    def article(**overrides):
        data = {
            "question_id": "NET-001", "title": "APP 绑定设备后显示离线",
            "category": "网络连接", "product_type": "云网管交换机",
            "applicable_models": ["通用"], "excluded_models": [],
            "english_expressions": ["The device appears offline."],
            "chinese_expressions": ["设备显示离线"],
            "standard_question": "客户绑定设备后显示离线。",
            "keywords": "设备离线，云管理，CLOUD", "conditions": ["绑定后显示离线。"],
            "causes": ["设备无法连接互联网。"], "general_steps": ["检查设备供电。"],
            "model_solutions": [{"model": "GS105", "steps": ["开启 CLOUD 设置。"]}],
            "risk": "重启会暂时中断网络。", "required_information": ["完整型号"],
            "escalation_conditions": ["处理后仍离线。"],
            "reply_restrictions": ["未确认型号时不得提供型号专用操作。"],
            "english_reply": "Please check the power and internet connection.",
            "source": {"name": "FAQ1.3", "version": "2026-07", "maintainer": "Support"},
        }
        data.update(overrides)
        return data

    def test_structured_article_create_read_update_and_validation(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            retriever = LexicalKnowledgeRetriever(directory)
            service = KnowledgeService(directory, retriever)
            article, filename, markdown = service.render_article(self.article())
            self.assertEqual(filename, "NET-001_APP_绑定设备后显示离线.md")
            self.assertIn("## 分型号解决方案", markdown)
            self.assertNotIn("请填写", markdown)
            created = service.save_article(article)
            self.assertTrue((directory / created["filename"]).is_file())
            self.assertEqual(service.read_article(filename)["article"]["title"], article["title"])
            updated = service.save_article(self.article(title="设备云管理离线"), filename)
            self.assertFalse((directory / filename).exists())
            self.assertTrue((directory / updated["filename"]).is_file())
            with self.assertRaisesRegex(ValueError, "文件已存在"):
                service.save_article(self.article(title="设备云管理离线"))
            with self.assertRaisesRegex(ValueError, "必填项"):
                service.render_article(self.article(title=""))
            with self.assertRaisesRegex(ValueError, "问题分类"):
                service.render_article(self.article(category="其他"))

    def test_plain_markdown_cannot_be_edited_as_structured_article(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            retriever = LexicalKnowledgeRetriever(directory)
            service = KnowledgeService(directory, retriever)
            (directory / "plain.md").write_text("# 普通知识\n不可覆盖的内容", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "不是由知识表单生成"):
                service.read_article("plain.md")
            with self.assertRaisesRegex(ValueError, "不能覆盖"):
                service.save_article(self.article(), "plain.md")

    def test_upload_delete_and_invalid_rollback(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            retriever = LexicalKnowledgeRetriever(directory)
            service = KnowledgeService(directory, retriever)
            self.assertEqual(service.upload([Upload("中文知识.md", "# GPS208\nGPS208 提供八个 POE 端口并支持标准网络供电。".encode())]), 1)
            self.assertTrue((directory / "中文知识.md").is_file())
            self.assertEqual(service.delete(["中文知识.md"]), 1)
            with self.assertRaises(ValueError):
                service.upload([Upload("bad.exe", b"bad")])
            self.assertEqual(service.list_files(), [])

    def test_multiple_files_are_uploaded_and_indexed_in_one_rebuild(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            retriever = LexicalKnowledgeRetriever(directory)
            rebuild = retriever.rebuild
            calls = []

            def tracked_rebuild():
                calls.append(True)
                return rebuild()

            retriever.rebuild = tracked_rebuild
            service = KnowledgeService(directory, retriever)
            count = service.upload([
                Upload("型号A.md", "# GPS208\nGPS208 提供八个 POE 端口并支持标准网络供电。".encode()),
                Upload("型号B.txt", "GPOE208 提供八个供电端口并支持标准网络设备连接。".encode()),
            ])
            self.assertEqual(count, 2)
            self.assertEqual(len(calls), 1)
            self.assertEqual([path.name for path in service.list_files()],
                             ["型号A.md", "型号B.txt"])
            self.assertGreaterEqual(retriever.get_stats()["entries"], 2)

    def test_duplicate_batch_rolls_back_without_partial_files(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            retriever = LexicalKnowledgeRetriever(directory)
            service = KnowledgeService(directory, retriever)
            with self.assertRaisesRegex(ValueError, "重名"):
                service.upload([
                    Upload("GPS208.md", b"# GPS208\nfirst content"),
                    Upload("gps208.MD", b"# GPS208\nsecond content"),
                ])
            self.assertEqual(service.list_files(), [])

    def test_invalid_file_in_batch_rolls_back_all_files(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            retriever = LexicalKnowledgeRetriever(directory)
            service = KnowledgeService(directory, retriever)
            with self.assertRaises(ValueError):
                service.upload([
                    Upload("valid.md", b"# GPS208\nvalid content"),
                    Upload("invalid.exe", b"invalid"),
                ])
            self.assertEqual(service.list_files(), [])


class AsyncEmailServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_mail_fetcher_async_methods_delegate_blocking_imap_calls(self):
        fetcher = MailFetcher("imap.example.com", 993, "agent@example.com", "secret")
        fetcher.connect = Mock()
        fetcher.fetch_unread = Mock(return_value=["mail"])
        fetcher.mark_seen = Mock()
        fetcher.disconnect = Mock()

        await fetcher.connect_async()
        emails = await fetcher.fetch_unread_async()
        await fetcher.mark_seen_async("42")
        await fetcher.disconnect_async()

        fetcher.connect.assert_called_once_with()
        fetcher.fetch_unread.assert_called_once_with()
        fetcher.mark_seen.assert_called_once_with("42")
        fetcher.disconnect.assert_called_once_with()
        self.assertEqual(emails, ["mail"])

    async def test_run_once_async_without_console_still_polls_imap(self):
        fetcher = SimpleNamespace(
            connect_async=AsyncMock(),
            fetch_unread_async=AsyncMock(return_value=[]),
            disconnect_async=AsyncMock(),
        )
        agent = object.__new__(EmailAgent)
        agent.logger = Mock()
        agent._fetcher = Mock(return_value=fetcher)
        stdout = sys.stdout
        try:
            sys.stdout = None
            results = await agent.run_once_async()
        finally:
            sys.stdout = stdout

        self.assertEqual(results, [])
        fetcher.connect_async.assert_awaited_once_with()
        fetcher.fetch_unread_async.assert_awaited_once_with()
        fetcher.disconnect_async.assert_awaited_once_with()

    async def test_run_once_async_marks_only_successful_messages_seen(self):
        messages = [
            SimpleNamespace(message_id="one", imap_uid="1"),
            SimpleNamespace(message_id="two", imap_uid="2"),
        ]
        fetcher = SimpleNamespace(
            connect_async=AsyncMock(),
            fetch_unread_async=AsyncMock(return_value=messages),
            mark_seen_async=AsyncMock(),
            disconnect_async=AsyncMock(),
        )
        agent = object.__new__(EmailAgent)
        agent.logger = Mock()
        agent._fetcher = Mock(return_value=fetcher)
        agent._process_emails_concurrently = AsyncMock(return_value=[True, False])

        results = await agent.run_once_async()

        self.assertEqual(results, [True, False])
        fetcher.mark_seen_async.assert_awaited_once_with("1")
        fetcher.disconnect_async.assert_awaited_once_with()

    async def test_concurrent_processing_respects_configured_limit(self):
        agent = object.__new__(EmailAgent)
        agent.max_concurrent = 2
        agent.logger = Mock()
        active = 0
        peak = 0

        async def process(_message):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return True

        agent._process_email_async = process
        agent._record_failure = Mock()
        messages = [
            SimpleNamespace(message_id=str(index), subject=f"subject {index}", sender="user@example.com")
            for index in range(5)
        ]

        results = await agent._process_emails_concurrently(messages)

        self.assertEqual(results, [True] * 5)
        self.assertEqual(peak, 2)


if __name__ == "__main__":
    unittest.main()
