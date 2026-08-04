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
        self.temp_root = Path(self.temp.name)
        knowledge_dir = self.temp_root / "knowledge"
        knowledge_dir.mkdir()
        (knowledge_dir / "base.md").write_text("# GPS208\n参数", encoding="utf-8")
        self.db = EmailDB(str(self.temp_root / "web.db"))
        sender = MailSender("test", 465, "agent@test", "secret")
        retriever = LexicalKnowledgeRetriever(knowledge_dir)
        review = ReviewService(self.db, sender, self.temp_root / "drafts")
        knowledge = KnowledgeService(knowledge_dir, retriever)
        self.db.mark_processed("m1", "GPS208", "customer@test", "技术参数咨询",
                               "neutral", "draft_ready", "客户问题")
        path = sender.save_draft("customer@test", "GPS208", "草稿", str(self.temp_root / "drafts"), "m1")
        self.db.update_status("m1", "draft_ready", "草稿", path)
        fake_fetcher = SimpleNamespace(connect=lambda: None, disconnect=lambda: None)
        fake_ai = SimpleNamespace(_call_llm=lambda prompt, max_tokens: "OK")
        agent = SimpleNamespace(
            db=self.db, sender=sender, review=review, retriever=retriever, kb=retriever,
            config={
                "workflow": {"mode": "semi_auto", "auto_reply_types": ["故障排查"]},
                "mail": {"account": "agent@test", "password": "secret", "imap_server": "imap.test",
                         "imap_port": 993, "smtp_server": "smtp.test", "smtp_port": 465, "poll_interval": 300},
                "ai": {"provider": "test", "model": "test-model", "api_base": "https://ai.test", "api_key": "secret"},
                "rag": {"mode": "lexical", "top_k": 3, "min_confidence": 0.5},
            },
            mode="semi_auto", ai=fake_ai, _fetcher=lambda: fake_fetcher,
        )
        self.app = create_app(agent, review, knowledge, True)
        self.client = self.app.test_client()
        login = self.client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        self.auth_headers = {"Authorization": f"Bearer {login.get_json()['token']}"}

    def tearDown(self):
        self.db.conn.close()
        self.temp.cleanup()

    def test_spa_routes_and_legacy_mail_redirects(self):
        for legacy, target in (("/mail", "/mails"), ("/mail/m1", "/mails/m1")):
            response = self.client.get(legacy, follow_redirects=False)
            self.assertEqual(response.status_code, 308)
            self.assertEqual(response.headers["Location"], target)
        for route in ("/mails", "/mails/m1", "/knowledge", "/settings"):
            response = self.client.get(route)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'<div id="app">', response.data)
            response.close()
        self.assertEqual(self.client.post("/mode").status_code, 405)
        self.assertEqual(self.client.post("/knowledge/upload").status_code, 405)

    def test_knowledge_api_manages_files_and_index(self):
        self.assertEqual(self.client.get("/api/knowledge", headers=self.auth_headers).status_code, 200)
        upload = self.client.post(
            "/api/knowledge/upload",
            data={"files": (io.BytesIO(
                "# POE208D\nPOE208D 提供八个 POE 端口并支持标准网络设备连接。".encode()
            ), "poe.md")},
            content_type="multipart/form-data", headers=self.auth_headers,
        )
        self.assertEqual(upload.status_code, 200)
        self.assertTrue((self.temp_root / "knowledge" / "poe.md").is_file())
        self.assertEqual(self.client.post("/api/knowledge/rebuild", headers=self.auth_headers).status_code, 200)
        deletion = self.client.post("/api/knowledge/delete", json={"names": ["poe.md"]}, headers=self.auth_headers)
        self.assertEqual(deletion.status_code, 200)
        self.assertFalse((self.temp_root / "knowledge" / "poe.md").exists())

    def test_settings_and_invalid_api_responses(self):
        settings = self.client.get("/api/settings", headers=self.auth_headers)
        self.assertEqual(settings.status_code, 200)
        self.assertNotIn("password", settings.get_json()["mail"])
        self.assertNotIn("api_key", settings.get_json()["ai"])
        self.assertEqual(self.client.put("/api/settings/mode", json={"mode": "unsafe"}, headers=self.auth_headers).status_code, 400)
        updated = self.client.put("/api/settings/mode", json={"mode": "full_auto"}, headers=self.auth_headers)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(self.db.get_setting("workflow_mode"), "full_auto")
        unknown = self.client.get("/api/not-a-real-endpoint", headers=self.auth_headers)
        self.assertEqual(unknown.status_code, 404)
        self.assertTrue(unknown.is_json)
        self.assertEqual(self.client.get("/api/mails?page=invalid", headers=self.auth_headers).status_code, 400)

    def test_repaired_message_is_accessible_and_deletable_via_api(self):
        self.db.conn.execute("""INSERT INTO processed_emails
            (message_id, subject, sender, received_at, status, original_body)
            VALUES ('', 'TEST-E2E-GPS208', 'legacy@test', '2026-07-27 18:02:21', 'draft_ready', '历史邮件')""")
        self.db.conn.commit()
        self.db._repair_missing_message_ids()
        item = self.db.conn.execute("SELECT * FROM processed_emails WHERE subject='TEST-E2E-GPS208'").fetchone()
        self.assertTrue(item["message_id"].startswith("generated-"))
        self.assertEqual(self.client.get(f"/api/mails/{item['message_id']}", headers=self.auth_headers).status_code, 200)
        response = self.client.post("/api/mails/delete", json={"message_ids": [item["message_id"]]}, headers=self.auth_headers)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.db.get_email(item["message_id"]))


if __name__ == "__main__":
    unittest.main()
