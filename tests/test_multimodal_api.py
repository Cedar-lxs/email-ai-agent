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


class MultimodalApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp.name)
        knowledge_dir = self.temp_root / "knowledge"
        knowledge_dir.mkdir()
        (knowledge_dir / "base.md").write_text("# GS105\n参数", encoding="utf-8")
        self.db = EmailDB(str(self.temp_root / "web.db"))
        sender = MailSender("test", 465, "agent@test", "secret")
        retriever = LexicalKnowledgeRetriever(knowledge_dir)
        review = ReviewService(self.db, sender, self.temp_root / "drafts")
        knowledge = KnowledgeService(knowledge_dir, retriever)
        self.db.mark_processed(
            "m1", "GS105 offline", "customer@test", "网络连接",
            "neutral", "draft_ready", "设备离线",
        )
        draft = sender.save_draft(
            "customer@test", "GS105 offline", "Please check the port.",
            str(self.temp_root / "drafts"), "m1",
        )
        self.db.update_status("m1", "draft_ready", "Please check the port.", draft)
        fake_fetcher = SimpleNamespace(connect=lambda: None, disconnect=lambda: None)
        fake_ai = SimpleNamespace(_call_llm=lambda prompt, max_tokens: "OK")
        media_root = self.temp_root / "media"
        attachment_root = self.temp_root / "attachments"
        agent = SimpleNamespace(
            db=self.db,
            sender=sender,
            review=review,
            retriever=retriever,
            kb=retriever,
            media_root=media_root,
            attachment_storage=SimpleNamespace(root=attachment_root),
            config={
                "workflow": {"mode": "semi_auto", "auto_reply_types": ["故障排查"]},
                "mail": {"account": "agent@test", "password": "secret", "imap_server": "imap.test",
                         "imap_port": 993, "smtp_server": "smtp.test", "smtp_port": 465, "poll_interval": 300},
                "ai": {"provider": "test", "model": "test-model", "api_base": "https://ai.test", "api_key": "secret"},
                "rag": {"mode": "lexical", "top_k": 3, "min_confidence": 0.5},
                "multimodal": {"enabled": True},
            },
            mode="semi_auto",
            ai=fake_ai,
            _fetcher=lambda: fake_fetcher,
        )
        self.app = create_app(agent, review, knowledge, True)
        self.client = self.app.test_client()
        setup = self.client.post("/api/auth/setup", json={
            "username": "admin",
            "password": "SecureAdmin!2026",
            "confirm_password": "SecureAdmin!2026",
        })
        self.assertEqual(setup.status_code, 201)
        self.auth_headers = {"X-CSRF-Token": setup.get_json()["csrf_token"]}

    def tearDown(self):
        self.db.conn.close()
        self.temp.cleanup()

    def test_database_round_trips_media_manifest_and_multimodal_trace(self):
        manifest = [{
            "media_id": "img1",
            "filename": "fault.jpg",
            "content_type": "image/jpeg",
            "source": "attachment",
            "path": str(self.temp_root / "media" / "fault.jpg"),
            "size_bytes": 10,
        }]
        trace = {
            "summary": "The port indicator appears off.",
            "product_identifiers": ["GS105"],
            "risk_signals": [],
            "confidence": 0.8,
        }

        self.db.save_media_manifest("m1", manifest)
        self.db.save_multimodal_trace("m1", trace)
        row = self.db.get_email("m1")

        self.assertEqual(self.db.parse_media_manifest(row), manifest)
        self.assertEqual(self.db.parse_multimodal_trace(row), trace)

    def test_mail_detail_includes_media_and_multimodal_trace(self):
        self.db.save_media_manifest("m1", [{
            "media_id": "img1",
            "filename": "fault.jpg",
            "content_type": "image/jpeg",
            "source": "attachment",
            "path": str(self.temp_root / "media" / "fault.jpg"),
            "size_bytes": 10,
        }])
        self.db.save_multimodal_trace("m1", {
            "summary": "The port indicator appears off.",
            "product_identifiers": ["GS105"],
            "model_numbers": ["GS105"],
            "device_ids": ["ID-7788"],
            "port_composition": ["5 x RJ45 Gigabit Ethernet"],
            "versions": ["Ver: 2.1"],
            "switch_management_type": "unmanaged",
            "label_text": ["Unmanaged Switch GS105 Ver: 2.1"],
            "risk_signals": [],
            "confidence": 0.8,
        })

        response = self.client.get("/api/mails/m1", headers=self.auth_headers)

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["media"][0]["media_id"], "img1")
        self.assertEqual(payload["multimodal"]["summary"], "The port indicator appears off.")
        self.assertEqual(payload["multimodal"]["model_numbers"], ["GS105"])
        self.assertEqual(payload["multimodal"]["device_ids"], ["ID-7788"])
        self.assertEqual(payload["multimodal"]["port_composition"], ["5 x RJ45 Gigabit Ethernet"])
        self.assertEqual(payload["multimodal"]["versions"], ["Ver: 2.1"])
        self.assertEqual(payload["multimodal"]["switch_management_type"], "unmanaged")
        self.assertEqual(payload["multimodal"]["label_text"], ["Unmanaged Switch GS105 Ver: 2.1"])

    def test_mail_detail_includes_decision_and_attachment_manifest(self):
        self.db.save_decision_trace("m1", {
            "action": "draft_ready",
            "blocking_reasons": ["semi_auto_mode"],
            "local_knowledge_hits": 2,
        })
        self.db.save_attachment_manifest("m1", [{
            "attachment_id": "report-1",
            "filename": "diagnosis.txt",
            "content_type": "text/plain",
            "size_bytes": 24,
            "extraction_status": "extracted",
            "extracted_text": "link state disconnected",
        }])

        response = self.client.get("/api/mails/m1", headers=self.auth_headers)

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["decision"]["action"], "draft_ready")
        self.assertEqual(payload["attachments"][0]["attachment_id"], "report-1")

    def test_attachment_route_serves_manifest_file_under_attachment_root(self):
        attachment_dir = self.temp_root / "attachments" / "m1"
        attachment_dir.mkdir(parents=True)
        attachment_path = attachment_dir / "diagnosis.txt"
        attachment_path.write_bytes(b"link state disconnected")
        self.db.save_attachment_manifest("m1", [{
            "attachment_id": "report-1",
            "filename": "diagnosis.txt",
            "content_type": "text/plain",
            "path": str(attachment_path),
            "size_bytes": 23,
        }])

        response = self.client.get(
            "/api/mails/m1/attachments/report-1", headers=self.auth_headers
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"link state disconnected")
        self.assertEqual(response.mimetype, "text/plain")
        response.close()

    def test_attachment_route_refuses_unlisted_or_outside_root_files(self):
        attachment_dir = self.temp_root / "attachments" / "m1"
        attachment_dir.mkdir(parents=True)
        listed_path = attachment_dir / "listed.txt"
        listed_path.write_bytes(b"listed")
        outside_path = self.temp_root / "outside.txt"
        outside_path.write_bytes(b"outside")
        self.db.save_attachment_manifest("m1", [{
            "attachment_id": "outside-1",
            "filename": "outside.txt",
            "content_type": "text/plain",
            "path": str(outside_path),
            "size_bytes": 7,
        }])

        unlisted = self.client.get(
            "/api/mails/m1/attachments/not-listed", headers=self.auth_headers
        )
        outside = self.client.get(
            "/api/mails/m1/attachments/outside-1", headers=self.auth_headers
        )

        self.assertEqual(unlisted.status_code, 404)
        self.assertEqual(outside.status_code, 404)

    def test_media_route_serves_manifest_file(self):
        directory = self.temp_root / "media" / "msg"
        directory.mkdir(parents=True)
        media_path = directory / "fault.jpg"
        media_path.write_bytes(b"image")
        self.db.save_media_manifest("m1", [{
            "media_id": "img1",
            "filename": "fault.jpg",
            "content_type": "image/jpeg",
            "source": "attachment",
            "path": str(media_path),
            "size_bytes": 5,
        }])

        response = self.client.get("/api/mails/m1/media/img1", headers=self.auth_headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"image")
        self.assertEqual(response.mimetype, "image/jpeg")
        response.close()


if __name__ == "__main__":
    unittest.main()
