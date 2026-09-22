import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email_agent.application.email_service import EmailAgent
from email_agent.application.mail_ingestion import MailIngestionService
from email_agent.application.mail_worker import MailJobWorker
from email_agent.domain.mail_jobs import JobStatus
from email_agent.domain.models import ParsedEmail
from email_agent.infrastructure.database import EmailDB
from email_agent.infrastructure.mail_fetcher import MailFetcher
from email_agent.infrastructure.mail_job_store import MailJobStore
from email_agent.infrastructure.raw_mail_spool import RawMailSpool


RAW_EMAIL = (
    b"From: Customer <customer@example.com>\r\n"
    b"Subject: Port down\r\n"
    b"Message-ID: <m1@example.com>\r\n"
    b"Date: Tue, 22 Sep 2026 10:00:00 +0800\r\n\r\n"
    b"Port 1 is down."
)


class FakeUIDFetcher:
    def __init__(self, uid_validity="7", messages=None, connect_error=None):
        self.uid_validity = uid_validity
        self.messages = messages or {}
        self.connect_error = connect_error
        self.seen_uids = []
        self.connect_count = 0

    async def connect_async(self):
        self.connect_count += 1
        if self.connect_error:
            raise self.connect_error

    async def disconnect_async(self):
        return None

    def select_folder(self, folder):
        return self.uid_validity

    def search_uids(self, after_uid):
        return sorted(uid for uid in self.messages if uid > after_uid)

    def fetch_uid(self, uid):
        return self.messages[uid]

    def mark_uid_seen(self, uid):
        self.seen_uids.append(int(uid))


class ReliableMailFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = EmailDB(str(self.root / "emails.db"))
        self.store = MailJobStore(self.db)
        self.spool = RawMailSpool(self.root / "spool")
        parser = MailFetcher(
            "imap.example.com", 993, "support@example.com", "secret"
        ).parse_raw_email
        self.agent = object.__new__(EmailAgent)
        self.agent.db = self.db
        self.agent.mail_account = "support@example.com"
        self.agent.mail_jobs = self.store
        self.agent.raw_spool = self.spool
        self.agent.ingestion = MailIngestionService(
            self.store, self.spool, self.agent.mail_account, "INBOX", batch_size=20
        )
        self.agent.worker = MailJobWorker(
            self.store, self.spool, parse_raw_email=parser,
            batch_size=20, max_concurrent=2, max_attempts=3,
            retry_delays=[60, 300, 1800], lease_seconds=60,
            worker_id="flow-worker",
        )
        self.agent.spool_retention_days = 30
        self.agent.logger = Mock()

    async def asyncTearDown(self):
        self.db.conn.close()
        self.temp.cleanup()

    def _discover(self, job_id, uid, uid_validity="7", raw=RAW_EMAIL):
        stored = self.spool.store(job_id, raw)
        self.store.record_discovery(
            job_id=job_id, account="support@example.com", folder="INBOX",
            uid_validity=uid_validity, imap_uid=uid,
            message_id=f"m-{job_id}@example.com", raw_path=stored.path,
            raw_sha256=stored.sha256,
        )
        return stored

    async def test_run_once_ingests_processes_and_marks_terminal_uid_seen(self):
        fetcher = FakeUIDFetcher(messages={42: RAW_EMAIL})
        self.agent._fetcher = Mock(return_value=fetcher)
        self.agent.process_job_email_async = AsyncMock(
            return_value=JobStatus.DRAFT_READY
        )
        results = await self.agent.run_once_async()
        self.assertEqual(results[0].status, JobStatus.DRAFT_READY)
        self.assertEqual(fetcher.seen_uids, [42])
        cursor = self.store.get_cursor("support@example.com", "INBOX")
        self.assertEqual(cursor.last_scanned_uid, 42)
        job = self.store.get_job(results[0].job_id)
        self.assertEqual(job["seen_result"], "marked")

    async def test_imap_failure_still_processes_preexisting_spool_job(self):
        self._discover("job-1", 1)
        fetcher = FakeUIDFetcher(connect_error=OSError("offline"))
        self.agent._fetcher = Mock(return_value=fetcher)
        self.agent.process_job_email_async = AsyncMock(
            return_value=JobStatus.DRAFT_READY
        )
        results = await self.agent.run_once_async()
        self.assertEqual(results[0].status, JobStatus.DRAFT_READY)
        self.agent.process_job_email_async.assert_awaited_once()

    async def test_old_uidvalidity_terminal_job_is_not_stored_as_seen_on_new_uid_space(self):
        self._discover("old-job", 1, uid_validity="7")
        self.store.claim_next("setup", lease_seconds=60)
        self.store.complete_job("old-job", JobStatus.DRAFT_READY, actor="setup")
        fetcher = FakeUIDFetcher(uid_validity="8")
        self.agent._fetcher = Mock(return_value=fetcher)
        self.agent.process_job_email_async = AsyncMock()
        await self.agent.run_once_async()
        self.assertEqual(fetcher.seen_uids, [])
        self.assertEqual(
            self.store.get_job("old-job")["seen_result"], "uidvalidity_expired"
        )

    async def test_processed_duplicate_maps_result_without_invoking_ai_flow(self):
        self.db.mark_processed(
            "same@example.com", "Question", "customer@example.com", status="replied"
        )
        email = ParsedEmail(
            message_id="same@example.com", subject="Question",
            sender="customer@example.com", sender_name="Customer",
            body_text="question", body_html="", received_at="2026-09-22",
        )
        self.agent._process_email_async = AsyncMock()
        result = await self.agent.process_job_email_async(email, "job-new")
        self.assertEqual(result, JobStatus.SENT)
        self.agent._process_email_async.assert_not_awaited()

    def test_cleanup_never_removes_unresolved_raw_mail(self):
        statuses = {
            "pending": JobStatus.PENDING,
            "processing": JobStatus.PROCESSING,
            "retry": JobStatus.RETRY_WAIT,
            "dead": JobStatus.DEAD_LETTER,
            "uncertain": JobStatus.AWAITING_CONFIRMATION,
        }
        paths = []
        for index, (name, target) in enumerate(statuses.items(), 1):
            stored = self._discover(name, index)
            paths.append(Path(stored.path))
            old = (datetime.now() - timedelta(days=90)).timestamp()
            os.utime(stored.path, (old, old))
            with self.store._connect() as conn:
                conn.execute(
                    "UPDATE mail_jobs SET status=?, updated_at=? WHERE id=?",
                    (target.value, "2026-01-01 00:00:00.000000", name),
                )
        self.agent._cleanup_expired_spool()
        self.assertTrue(all(path.exists() for path in paths))


if __name__ == "__main__":
    unittest.main()
