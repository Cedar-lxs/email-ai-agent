import hashlib
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email_agent.infrastructure.raw_mail_spool import RawMailSpool


class RawMailSpoolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "spool"
        self.now = datetime(2026, 9, 22, 10, 0, 0)
        self.spool = RawMailSpool(self.root, clock=lambda: self.now)

    def tearDown(self):
        self.temp.cleanup()

    def _write_with_age(self, name, **age):
        path = self.root / "2026-09" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"raw")
        modified = (self.now - timedelta(**age)).timestamp()
        os.utime(path, (modified, modified))
        return path.resolve()

    def test_store_uses_generated_name_and_returns_verified_hash(self):
        raw = b"Subject: Test\r\n\r\nBody"
        stored = self.spool.store("job-123", raw)
        self.assertEqual(Path(stored.path).name, "job-123.eml")
        self.assertEqual(self.spool.read(stored.path), raw)
        self.assertEqual(stored.sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(stored.size_bytes, len(raw))
        self.assertFalse(list(self.root.rglob("*.tmp")))

    def test_failed_replace_removes_temporary_file_and_leaves_no_final(self):
        with patch(
            "email_agent.infrastructure.raw_mail_spool.os.replace",
            side_effect=OSError("disk"),
        ):
            with self.assertRaisesRegex(OSError, "disk"):
                self.spool.store("job-123", b"raw")
        self.assertFalse(list(self.root.rglob("*job-123*")))

    def test_cleanup_only_deletes_unreferenced_files_older_than_grace_period(self):
        old_orphan = self._write_with_age("old.eml", days=2)
        old_referenced = self._write_with_age("kept.eml", days=2)
        fresh_orphan = self._write_with_age("fresh.eml", minutes=1)
        deleted = self.spool.cleanup_orphans(
            {str(old_referenced)}, grace_seconds=3600
        )
        self.assertEqual(deleted, [str(old_orphan)])
        self.assertTrue(old_referenced.exists())
        self.assertTrue(fresh_orphan.exists())

    def test_job_id_is_sanitized_and_cannot_escape_root(self):
        stored = self.spool.store("../../outside", b"raw")
        resolved = Path(stored.path).resolve()
        self.assertIn(self.root.resolve(), resolved.parents)
        self.assertEqual(resolved.name, "______outside.eml")

    def test_read_and_discard_reject_paths_outside_root(self):
        outside = Path(self.temp.name) / "outside.eml"
        outside.write_bytes(b"private")
        with self.assertRaisesRegex(ValueError, "暂存目录"):
            self.spool.read(outside)
        with self.assertRaisesRegex(ValueError, "暂存目录"):
            self.spool.discard(outside)
        self.assertTrue(outside.exists())


if __name__ == "__main__":
    unittest.main()
