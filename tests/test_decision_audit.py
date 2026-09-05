import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.email_agent.application.decision_audit import build_decision_trace
from src.email_agent.infrastructure.database import EmailDB


class DecisionAuditDatabaseTests(unittest.TestCase):
    def test_stores_and_reads_decision_trace_and_attachment_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            db = EmailDB(str(Path(directory) / "emails.db"))
            message_id = "message-1"
            db.mark_processed(message_id, "Subject", "customer@example.com")
            decision_trace = {"priority": "high", "signals": ["refund", "order"]}
            attachment_manifest = [{"filename": "photo.jpg", "content_type": "image/jpeg"}]

            try:
                db.save_decision_trace(message_id, decision_trace)
                db.save_attachment_manifest(message_id, attachment_manifest)

                row = db.get_email(message_id)
                self.assertEqual(db.parse_decision_trace(row), decision_trace)
                self.assertEqual(db.parse_attachment_manifest(row), attachment_manifest)
            finally:
                db.conn.close()

    def test_migrates_existing_database_in_place(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "legacy.db"
            connection = sqlite3.connect(db_path)
            connection.execute("""
                CREATE TABLE processed_emails (
                    message_id TEXT PRIMARY KEY,
                    subject TEXT,
                    sender TEXT,
                    received_at TEXT,
                    intent TEXT,
                    sentiment TEXT,
                    status TEXT DEFAULT 'pending',
                    draft_text TEXT,
                    replied_at TEXT,
                    notes TEXT,
                    original_body TEXT,
                    draft_path TEXT,
                    in_reply_to TEXT,
                    last_error TEXT,
                    retry_count INTEGER DEFAULT 0,
                    retrieval_trace TEXT DEFAULT '',
                    media_manifest TEXT DEFAULT '',
                    multimodal_trace TEXT DEFAULT ''
                )
            """)
            connection.execute(
                "INSERT INTO processed_emails (message_id, subject, sender) VALUES (?, ?, ?)",
                ("legacy-1", "Legacy", "customer@example.com"),
            )
            connection.commit()
            connection.close()

            db = EmailDB(str(db_path))
            try:
                columns = {
                    row[1]
                    for row in db.conn.execute("PRAGMA table_info(processed_emails)")
                }
                self.assertIn("decision_trace", columns)
                self.assertIn("attachment_manifest", columns)
                row = db.get_email("legacy-1")
                self.assertEqual(db.parse_decision_trace(row), {})
                self.assertEqual(db.parse_attachment_manifest(row), [])
            finally:
                db.conn.close()

    def test_parse_helpers_reject_malformed_or_wrong_json_types(self):
        row = {"decision_trace": "not-json", "attachment_manifest": json.dumps({"file": "x"})}

        self.assertEqual(EmailDB.parse_decision_trace(row), {})
        self.assertEqual(EmailDB.parse_attachment_manifest(row), [])


class DecisionAuditBuilderTests(unittest.TestCase):
    def test_auto_sent_low_risk_technical_mail_has_no_blocking_reasons(self):
        trace = build_decision_trace(
            action="auto_sent",
            mode="full_auto",
            intent="network_connection",
            sentiment="neutral",
            urgency="low",
            auto_allowed_intent=True,
            local_knowledge_hits=2,
            used_web_search=False,
            media_count=0,
            attachment_count=0,
            known_intents={"network_connection"},
        )

        self.assertEqual(trace["blocking_reasons"], [])
        self.assertEqual(trace["signals"], [])

    def test_draft_in_semi_auto_mode_records_mode_blocking_reason(self):
        trace = build_decision_trace(
            action="draft_ready",
            mode="semi_auto",
            intent="network_connection",
            sentiment="neutral",
            urgency="low",
            auto_allowed_intent=True,
            local_knowledge_hits=1,
            used_web_search=False,
            media_count=0,
            attachment_count=0,
            known_intents={"network_connection"},
        )

        self.assertIn("semi_auto_mode", trace["blocking_reasons"])

    def test_escalated_business_mail_records_high_risk_reason(self):
        trace = build_decision_trace(
            action="escalated",
            mode="full_auto",
            intent="business_question",
            sentiment="neutral",
            urgency="high",
            auto_allowed_intent=False,
            local_knowledge_hits=0,
            used_web_search=False,
            media_count=0,
            attachment_count=0,
            known_intents={"business_question"},
        )

        self.assertIn("business_or_high_risk", trace["blocking_reasons"])

    def test_sanitizes_untrusted_labels_before_persisting_trace(self):
        trace = build_decision_trace(
            action="auto_sent\napi_key=sk-1234567890abcdef",
            mode="full_auto",
            intent="unknown",
            sentiment="api_key=abcd1234",
            urgency="short reasoning text",
            auto_allowed_intent=True,
            local_knowledge_hits=1,
            used_web_search=False,
            media_count=0,
            attachment_count=0,
            known_intents={"网络连接"},
            blocking_reasons=["reason\nsecret-token-1234567890"],
            signals=["api_key=abcd1234", "media_present"],
        )

        self.assertEqual(trace["action"], "unknown")
        self.assertEqual(trace["sentiment"], "unknown")
        self.assertEqual(trace["urgency"], "unknown")
        self.assertEqual(trace["blocking_reasons"], ["unknown"])
        self.assertEqual(trace["signals"], ["unknown", "media_present"])

    def test_missing_knowledge_escalation_does_not_invent_business_reason(self):
        trace = build_decision_trace(
            action="escalated",
            mode="full_auto",
            intent="设备离线",
            sentiment="neutral",
            urgency="low",
            auto_allowed_intent=False,
            local_knowledge_hits=0,
            used_web_search=False,
            media_count=0,
            attachment_count=0,
            known_intents={"设备离线"},
            blocking_reasons=["missing_knowledge"],
        )

        self.assertEqual(trace["blocking_reasons"], ["missing_knowledge"])


if __name__ == "__main__":
    unittest.main()
