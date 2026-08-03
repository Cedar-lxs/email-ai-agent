import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email_agent.application.knowledge_service import KnowledgeService
from email_agent.application.review_service import ReviewService
from email_agent.infrastructure.database import EmailDB
from email_agent.infrastructure.knowledge.lexical import LexicalKnowledgeRetriever
from email_agent.infrastructure.mail_sender import MailSender
from email_agent.web.app import create_app


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.temp_root = root
        knowledge_dir = root / "knowledge"
        knowledge_dir.mkdir()
        (knowledge_dir / "base.md").write_text("# GPS208\n参数", encoding="utf-8")
        self.db = EmailDB(str(root / "web.db"))
        sender = MailSender("test", 465, "agent@test", "secret")
        retriever = LexicalKnowledgeRetriever(knowledge_dir)
        review = ReviewService(self.db, sender, root / "drafts")
        knowledge = KnowledgeService(knowledge_dir, retriever)
        self.db.mark_processed("m1", "GPS208", "customer@test", "技术参数咨询",
                               "neutral", "draft_ready", "客户问题")
        path = sender.save_draft("customer@test", "GPS208", "草稿", str(root / "drafts"), "m1")
        self.db.update_status("m1", "draft_ready", "草稿", path)
        fake_fetcher = SimpleNamespace(connect=lambda: None, disconnect=lambda: None)
        fake_ai = SimpleNamespace(_call_llm=lambda prompt, max_tokens: "OK")
        agent = SimpleNamespace(
            db=self.db, sender=sender, review=review, retriever=retriever, kb=retriever,
            config={
                "workflow": {"mode": "semi_auto", "auto_reply_types": ["故障排查"]},
                "mail": {"account": "agent@test", "password": "secret",
                         "imap_server": "imap.test", "imap_port": 993,
                         "smtp_server": "smtp.test", "smtp_port": 465,
                         "poll_interval": 300},
                "ai": {"provider": "test", "model": "test-model",
                       "api_base": "https://ai.test", "api_key": "secret"},
                "rag": {"mode": "lexical", "top_k": 3, "min_confidence": 0.5},
            },
            mode="semi_auto", ai=fake_ai, _fetcher=lambda: fake_fetcher,
        )
        self.app = create_app(agent, review, knowledge, True)
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["csrf"] = "token"
        login = self.client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin123"}
        )
        self.auth_headers = {"Authorization": f"Bearer {login.get_json()['token']}"}

    def tearDown(self):
        self.db.conn.close()
        self.temp.cleanup()

    def test_existing_urls_and_csrf(self):
        self.assertEqual(self.client.get("/mail").status_code, 200)
        self.assertEqual(self.client.get("/mail/m1").status_code, 200)
        self.assertEqual(self.client.get("/knowledge").status_code, 200)
        self.client.post("/mode", data={"csrf": "bad", "mode": "full_auto"})
        self.assertEqual(self.db.get_setting("workflow_mode", "semi_auto"), "semi_auto")
        self.client.post("/mode", data={"csrf": "token", "mode": "full_auto"})
        self.assertEqual(self.db.get_setting("workflow_mode"), "full_auto")

    def test_multiple_file_upload_via_http_builds_index(self):
        response = self.client.post(
            "/knowledge/upload",
            data={
                "csrf": "token",
                "files": [
                    (io.BytesIO("# GPS204\nGPS204 提供四个 POE 端口并支持标准网络供电。".encode()), "gps204.md"),
                    (io.BytesIO("POE208D 提供八个 POE 端口并支持标准网络设备连接。".encode()), "poe208d.txt"),
                ],
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("成功上传 2 个知识文件".encode(), response.data)
        self.assertTrue((self.temp_root / "knowledge" / "gps204.md").is_file())
        self.assertTrue((self.temp_root / "knowledge" / "poe208d.txt").is_file())
        self.assertGreaterEqual(self.app.extensions["services"].knowledge.retriever.get_stats()["entries"], 2)

    def test_vue_knowledge_api_manages_files_and_index(self):
        overview = self.client.get("/api/knowledge", headers=self.auth_headers)
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.get_json()["files"][0]["name"], "base.md")

        upload = self.client.post(
            "/api/knowledge/upload",
            data={"files": (io.BytesIO(
                "# POE208D\nPOE208D 提供八个 POE 端口并支持标准网络设备连接。".encode()
            ), "poe.md")},
            content_type="multipart/form-data",
            headers=self.auth_headers,
        )
        self.assertEqual(upload.status_code, 200)
        self.assertTrue((self.temp_root / "knowledge" / "poe.md").is_file())

        rebuild = self.client.post("/api/knowledge/rebuild", headers=self.auth_headers)
        self.assertEqual(rebuild.status_code, 200)
        deletion = self.client.post(
            "/api/knowledge/delete", json={"names": ["poe.md"]}, headers=self.auth_headers
        )
        self.assertEqual(deletion.status_code, 200)
        self.assertFalse((self.temp_root / "knowledge" / "poe.md").exists())

    def test_structured_knowledge_article_api(self):
        article = {
            "question_id": "NET-009", "title": "设备云管理离线", "category": "网络连接",
            "product_type": "云网管交换机", "applicable_models": ["通用"],
            "english_expressions": ["The device is offline."], "chinese_expressions": ["设备离线"],
            "standard_question": "设备绑定后显示离线。", "keywords": "离线，CLOUD",
            "conditions": ["绑定后离线"], "causes": ["互联网连接异常"],
            "general_steps": ["检查互联网连接。"], "model_solutions": [],
            "risk": "无", "required_information": ["型号"],
            "escalation_conditions": ["处理后仍异常"], "reply_restrictions": [],
            "english_reply": "Please check the internet connection.",
            "source": {"name": "FAQ", "version": "2026", "maintainer": "Support"},
        }
        preview = self.client.post("/api/knowledge/articles/preview", json=article,
                                   headers=self.auth_headers)
        self.assertEqual(preview.status_code, 200)
        self.assertNotIn("请填写", preview.get_json()["markdown"])
        created = self.client.post("/api/knowledge/articles", json=article,
                                   headers=self.auth_headers)
        self.assertEqual(created.status_code, 201)
        filename = created.get_json()["filename"]
        overview = self.client.get("/api/knowledge", headers=self.auth_headers).get_json()
        row = next(item for item in overview["files"] if item["name"] == filename)
        self.assertTrue(row["editable"])
        loaded = self.client.get(f"/api/knowledge/articles/{filename}", headers=self.auth_headers)
        self.assertEqual(loaded.get_json()["article"]["question_id"], "NET-009")
        article["title"] = "设备连接云平台失败"
        updated = self.client.put(f"/api/knowledge/articles/{filename}", json=article,
                                  headers=self.auth_headers)
        self.assertEqual(updated.status_code, 200)
        self.assertNotEqual(updated.get_json()["filename"], filename)
        invalid = self.client.post("/api/knowledge/articles", json={"title": "缺字段"},
                                   headers=self.auth_headers)
        self.assertEqual(invalid.status_code, 400)
        plain = self.client.get("/api/knowledge/articles/base.md", headers=self.auth_headers)
        self.assertEqual(plain.status_code, 400)

    def test_vue_settings_api_hides_secrets_and_updates_mode(self):
        response = self.client.get("/api/settings", headers=self.auth_headers)
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertNotIn("password", payload["mail"])
        self.assertNotIn("api_key", payload["ai"])

        invalid = self.client.put(
            "/api/settings/mode", json={"mode": "unsafe"}, headers=self.auth_headers
        )
        self.assertEqual(invalid.status_code, 400)
        updated = self.client.put(
            "/api/settings/mode", json={"mode": "full_auto"}, headers=self.auth_headers
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(self.app.email_agent.mode, "full_auto")
        self.assertEqual(self.db.get_setting("workflow_mode"), "full_auto")

        self.assertEqual(self.client.post(
            "/api/settings/test-mail", headers=self.auth_headers).status_code, 200)
        self.assertEqual(self.client.post(
            "/api/settings/test-ai", headers=self.auth_headers).status_code, 200)

    def test_unknown_api_never_falls_back_to_spa_html(self):
        response = self.client.get("/api/not-a-real-endpoint", headers=self.auth_headers)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(response.is_json)
        self.assertIn("API 接口不存在", response.get_json()["error"])

    def test_invalid_mail_pagination_returns_json_error(self):
        response = self.client.get(
            "/api/mails?page=invalid", headers=self.auth_headers
        )
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.is_json)

    def test_missing_message_id_is_repaired_and_deletable(self):
        self.db.conn.execute("""
            INSERT INTO processed_emails
                (message_id, subject, sender, received_at, status, original_body)
            VALUES ('', 'TEST-E2E-GPS208', 'legacy@test', '2026-07-27 18:02:21',
                    'draft_ready', '历史邮件')
        """)
        self.db.conn.commit()
        self.db._repair_missing_message_ids()
        item = self.db.conn.execute(
            "SELECT * FROM processed_emails WHERE subject='TEST-E2E-GPS208'"
        ).fetchone()
        self.assertTrue(item["message_id"].startswith("generated-"))
        self.assertEqual(self.client.get(f"/mail/{item['message_id']}").status_code, 200)
        response = self.client.post(
            "/mail/delete",
            data={"csrf": "token", "message_ids": item["message_id"]},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.db.get_email(item["message_id"]))


if __name__ == "__main__":
    unittest.main()
