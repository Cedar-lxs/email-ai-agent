import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email_agent.domain.mail_jobs import DeliveryStatus, JobStatus
from email_agent.infrastructure.database import EmailDB
from email_agent.infrastructure.mail_job_store import MailJobStore


class FakeClock:
    def __init__(self):
        self.value = datetime(2026, 9, 21, 9, 0, 0)

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


class MailJobStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "emails.db"
        self.db = EmailDB(str(self.db_path))
        self.clock = FakeClock()
        self.store = MailJobStore(self.db, clock=self.clock)

    def tearDown(self):
        self.db.conn.close()
        self.temp.cleanup()

    def _discover(self, job_id="job-1", uid=1, message_id=None):
        return self.store.record_discovery(
            job_id=job_id,
            account="support@example.com",
            folder="INBOX",
            uid_validity="7",
            imap_uid=uid,
            message_id=message_id or f"m-{uid}@example.com",
            raw_path=f"spool/{job_id}.eml",
            raw_sha256=f"sha-{uid}",
        )[0]

    def _create_sending_delivery(self, job_id, uid):
        self._discover(job_id, uid)
        self.store.claim_next("worker-a", lease_seconds=1)
        delivery_id = self.store.create_delivery(
            job_id=job_id,
            business_message_id=f"m-{uid}@example.com",
            email_message_id=f"<delivery-{uid}@example.com>",
            recipient="customer@example.com",
            subject="Re: test",
            body="reply",
            body_sha256="hash",
            created_by="auto",
        )
        self.store.mark_delivery_sending(delivery_id, lease_seconds=1)
        return delivery_id

    def test_record_discovery_is_idempotent_and_advances_cursor_atomically(self):
        first, created = self.store.record_discovery(
            job_id="job-1", account="support@example.com", folder="INBOX",
            uid_validity="7", imap_uid=42, message_id="m1@example.com",
            raw_path="spool/job-1.eml", raw_sha256="abc",
        )
        duplicate, created_again = self.store.record_discovery(
            job_id="job-2", account="support@example.com", folder="INBOX",
            uid_validity="7", imap_uid=42, message_id="m1@example.com",
            raw_path="spool/job-2.eml", raw_sha256="abc",
        )
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first["id"], duplicate["id"])
        cursor = self.store.get_cursor("support@example.com", "INBOX")
        self.assertEqual(cursor.last_scanned_uid, 42)
        self.assertEqual(cursor.uid_validity, "7")
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM mail_jobs").fetchone()[0], 1
        )

    def test_two_store_connections_claim_a_job_only_once(self):
        other_db = EmailDB(str(self.db_path))
        try:
            other = MailJobStore(other_db, clock=self.clock)
            self._discover("job-1", 1)
            claims = [
                self.store.claim_next("worker-a", lease_seconds=60),
                other.claim_next("worker-b", lease_seconds=60),
            ]
        finally:
            other_db.conn.close()
        self.assertEqual([row["id"] for row in claims if row], ["job-1"])

    def test_expired_processing_retries_but_expired_sending_requires_confirmation(self):
        self._discover("processing-job", 1)
        self.store.claim_next("worker-a", lease_seconds=1)
        self.clock.advance(seconds=2)
        self.store.recover_expired()
        self.assertEqual(
            self.store.get_job("processing-job")["status"], JobStatus.RETRY_WAIT
        )

        delivery_id = self._create_sending_delivery("sending-job", 2)
        self.clock.advance(seconds=2)
        self.store.recover_expired()
        self.assertEqual(
            self.store.get_delivery(delivery_id)["status"], DeliveryStatus.UNCERTAIN
        )
        self.assertEqual(
            self.store.get_job("sending-job")["status"],
            JobStatus.AWAITING_CONFIRMATION,
        )

    def test_complete_job_rejects_illegal_transition(self):
        self._discover("job-1", 1)
        with self.assertRaisesRegex(ValueError, "非法状态转换"):
            self.store.complete_job("job-1", JobStatus.SENT, actor="worker-a")

    def test_fail_job_uses_backoff_and_dead_letters_third_attempt(self):
        self._discover("job-1", 1)
        for attempt, delay in enumerate((60, 300), 1):
            self.store.claim_next("worker-a", lease_seconds=60)
            status = self.store.fail_job(
                "job-1", "temporary", max_attempts=3,
                retry_delays=[60, 300, 1800], actor="worker-a",
            )
            self.assertEqual(status, JobStatus.RETRY_WAIT)
            row = self.store.get_job("job-1")
            expected = self.clock.value + timedelta(seconds=delay)
            self.assertEqual(row["next_attempt_at"], self.store.timestamp(expected))
            self.clock.advance(seconds=delay)

        self.store.claim_next("worker-a", lease_seconds=60)
        status = self.store.fail_job(
            "job-1", "x" * 3000, max_attempts=3,
            retry_delays=[60, 300, 1800], actor="worker-a",
        )
        self.assertEqual(status, JobStatus.DEAD_LETTER)
        self.assertEqual(len(self.store.get_job("job-1")["last_error"]), 1000)

    def test_events_are_append_only_and_capture_transitions(self):
        self._discover("job-1", 1)
        self.store.claim_next("worker-a", lease_seconds=60)
        self.store.complete_job("job-1", JobStatus.DRAFT_READY, actor="worker-a")
        events = self.store.get_events("job-1")
        self.assertEqual(
            [row["event_type"] for row in events],
            ["discovered", "claimed", "completed"],
        )
        self.assertEqual(events[-1]["from_status"], JobStatus.PROCESSING)
        self.assertEqual(events[-1]["to_status"], JobStatus.DRAFT_READY)

    def test_reset_cursor_changes_uidvalidity_without_deleting_old_jobs(self):
        self._discover("job-1", 42)
        self.store.reset_cursor("support@example.com", "INBOX", "8")
        cursor = self.store.get_cursor("support@example.com", "INBOX")
        self.assertEqual(cursor.uid_validity, "8")
        self.assertEqual(cursor.last_scanned_uid, 0)
        self.assertIsNotNone(self.store.get_job("job-1"))

    def test_active_or_accepted_delivery_blocks_unapproved_duplicate(self):
        self._discover("job-1", 1)
        self.store.claim_next("worker-a", lease_seconds=60)
        delivery_id = self.store.create_delivery(
            job_id="job-1", business_message_id="m-1@example.com",
            email_message_id="<d1@example.com>", recipient="c@example.com",
            subject="Re: q", body="a", body_sha256="hash", created_by="auto",
        )
        with self.assertRaisesRegex(ValueError, "投递"):
            self.store.create_delivery(
                job_id="job-1", business_message_id="m-1@example.com",
                email_message_id="<d2@example.com>", recipient="c@example.com",
                subject="Re: q", body="a", body_sha256="hash", created_by="auto",
            )
        self.store.mark_delivery_sending(delivery_id, lease_seconds=60)
        self.store.accept_delivery(delivery_id)
        with self.assertRaisesRegex(ValueError, "投递"):
            self.store.create_delivery(
                job_id="job-1", business_message_id="m-1@example.com",
                email_message_id="<d3@example.com>", recipient="c@example.com",
                subject="Re: q", body="a", body_sha256="hash", created_by="auto",
            )

    def test_initializing_store_preserves_legacy_processed_rows(self):
        legacy_path = Path(self.temp.name) / "legacy.db"
        legacy = EmailDB(str(legacy_path))
        legacy.mark_processed(
            "legacy@example.com", "Old subject", "old@example.com",
            status="replied", original_body="old body",
        )
        before = dict(legacy.get_email("legacy@example.com"))
        MailJobStore(legacy, clock=self.clock)
        after = dict(legacy.get_email("legacy@example.com"))
        legacy.conn.close()
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
