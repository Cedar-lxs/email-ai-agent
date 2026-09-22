import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email_agent.application.mail_ingestion import MailIngestionService
from email_agent.infrastructure.database import EmailDB
from email_agent.infrastructure.mail_fetcher import MailFetcher
from email_agent.infrastructure.mail_job_store import MailJobStore
from email_agent.infrastructure.raw_mail_spool import RawMailSpool, StoredRawMail


RAW_EMAIL = (
    b"From: Customer <customer@example.com>\r\n"
    b"To: support@example.com\r\n"
    b"Subject: Test switch\r\n"
    b"Message-ID: <m1@example.com>\r\n"
    b"Date: Tue, 22 Sep 2026 10:00:00 +0800\r\n\r\n"
    b"The port is down."
)


class FakeIMAP:
    def __init__(self, uid_validity=b"91", search_result=b"101 102", raw=RAW_EMAIL):
        self.uid_validity = uid_validity
        self.search_result = search_result
        self.raw = raw
        self.uid_calls = []
        self.selected = []

    def select(self, folder):
        self.selected.append(folder)
        return "OK", [b"2"]

    def response(self, name):
        return name, [self.uid_validity]

    def uid(self, command, *args):
        self.uid_calls.append((command, *args))
        if command == "SEARCH":
            return "OK", [self.search_result]
        if command == "FETCH":
            return "OK", [(b"1 (BODY[] {10})", self.raw), b")"]
        if command == "STORE":
            return "OK", [b""]
        raise AssertionError(command)


class FakeFetcher:
    def __init__(self, uid_validity="91", uids=None, messages=None):
        self.uid_validity = uid_validity
        self.uids = list(uids or [])
        self.messages = messages or {uid: RAW_EMAIL for uid in self.uids}
        self.search_after = None

    def select_folder(self, folder):
        return self.uid_validity

    def search_uids(self, after_uid):
        self.search_after = after_uid
        return list(self.uids)

    def fetch_uid(self, uid):
        return self.messages[uid]


class MailIngestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = EmailDB(str(root / "emails.db"))
        self.store = MailJobStore(self.db)
        self.spool = RawMailSpool(root / "spool")
        self.ingestion = MailIngestionService(
            self.store, self.spool, "support@example.com", "INBOX", batch_size=2
        )

    def tearDown(self):
        self.db.conn.close()
        self.temp.cleanup()

    @staticmethod
    def _fetcher(connection):
        fetcher = MailFetcher(
            "imap.example.com", 993, "support@example.com", "secret"
        )
        fetcher._conn = connection
        return fetcher

    def _set_cursor(self, uid_validity, uid):
        self.store.reset_cursor("support@example.com", "INBOX", uid_validity)
        self.store.advance_cursor("support@example.com", "INBOX", uid_validity, uid)

    def test_fetcher_uses_uid_search_fetch_and_store(self):
        connection = FakeIMAP(uid_validity=b"91", search_result=b"101 102")
        fetcher = self._fetcher(connection)
        self.assertEqual(fetcher.select_folder("INBOX"), "91")
        self.assertEqual(fetcher.search_uids(after_uid=100), [101, 102])
        self.assertIn(("SEARCH", None, "UID 101:*"), connection.uid_calls)
        self.assertEqual(fetcher.fetch_uid(101), RAW_EMAIL)
        fetcher.mark_uid_seen(101)
        self.assertIn(("STORE", "101", "+FLAGS", "\\Seen"), connection.uid_calls)
        self.assertNotIn(("STORE", "101", "+FLAGS", "\\Seen"), connection.uid_calls[:-1])

    def test_malformed_uidvalidity_stops_without_changing_cursor(self):
        self._set_cursor("90", 100)
        fetcher = FakeFetcher(uid_validity="not-a-number")
        with self.assertRaisesRegex(RuntimeError, "UIDVALIDITY"):
            self.ingestion.ingest(fetcher)
        self.assertEqual(
            self.store.get_cursor("support@example.com", "INBOX").last_scanned_uid,
            100,
        )

    def test_spool_failure_does_not_advance_past_failed_uid(self):
        fetcher = FakeFetcher(uid_validity="91", uids=[101, 102])
        real_store = self.spool.store
        self.spool.store = Mock(side_effect=[
            real_store("first", RAW_EMAIL),
            OSError("disk full"),
        ])
        with self.assertRaisesRegex(OSError, "disk full"):
            self.ingestion.ingest(fetcher)
        self.assertEqual(
            self.store.get_cursor("support@example.com", "INBOX").last_scanned_uid,
            101,
        )

    def test_uidvalidity_change_resets_uid_scan(self):
        self._set_cursor(uid_validity="90", uid=500)
        fetcher = FakeFetcher(uid_validity="91", uids=[1])
        result = self.ingestion.ingest(fetcher)
        self.assertEqual(fetcher.search_after, 0)
        self.assertEqual(result.scanned_uids, (1,))
        self.assertEqual(self.store.count_jobs(), 1)
        cursor = self.store.get_cursor("support@example.com", "INBOX")
        self.assertEqual(cursor.uid_validity, "91")
        self.assertEqual(cursor.last_scanned_uid, 1)

    def test_duplicate_discovery_discards_new_unused_spool_file(self):
        first = self.ingestion.ingest(FakeFetcher(uid_validity="91", uids=[1]))
        first_job = self.store.get_job(first.discovered_job_ids[0])
        first_path = Path(first_job["raw_path"])
        second = self.ingestion.ingest(FakeFetcher(uid_validity="91", uids=[1]))
        self.assertEqual(second.discovered_job_ids, ())
        self.assertTrue(first_path.exists())
        self.assertEqual(len(list(self.spool.root.rglob("*.eml"))), 1)

    def test_batch_limit_and_uid_sorting_are_applied(self):
        result = self.ingestion.ingest(
            FakeFetcher(uid_validity="91", uids=[103, 101, 102])
        )
        self.assertEqual(result.scanned_uids, (101, 102))
        self.assertEqual(self.store.count_jobs(), 2)

    def test_missing_message_id_uses_raw_sha256_fallback(self):
        raw = RAW_EMAIL.replace(b"Message-ID: <m1@example.com>\r\n", b"")
        result = self.ingestion.ingest(
            FakeFetcher(uid_validity="91", uids=[1], messages={1: raw})
        )
        job = self.store.get_job(result.discovered_job_ids[0])
        digest = hashlib.sha256(raw).hexdigest()[:32]
        self.assertEqual(job["message_id"], f"generated-{digest}@local")


if __name__ == "__main__":
    unittest.main()
