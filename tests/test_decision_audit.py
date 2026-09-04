import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
