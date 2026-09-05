import base64
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email_agent.domain.models import EmailAttachment
from email_agent.infrastructure.attachment_storage import (
    AttachmentStorage,
    format_attachment_context,
)


class AttachmentStorageTests(unittest.TestCase):
    def test_saves_supported_attachments_in_hashed_message_directory(self):
        with tempfile.TemporaryDirectory() as root:
            attachments = [
                EmailAttachment(
                    "a1",
                    "report.pdf",
                    "application/pdf",
                    "attachment",
                    data=base64.b64decode(
                        "JVBERi0xLjMKJeLjz9MKMSAwIG9iago8PAovUHJvZHVjZXIgKHB5cGRmKQo+Pgpl"
                        "bmRvYmoKMiAwIG9iago8PAovVHlwZSAvUGFnZXMKL0NvdW50IDEKL0tpZHMgWyA0"
                        "IDAgUiBdCj4+CmVuZG9iagozIDAgb2JqCjw8Ci9UeXBlIC9DYXRhbG9nCi9QYWdl"
                        "cyAyIDAgUgo+PgplbmRvYmoKNCAwIG9iago8PAovVHlwZSAvUGFnZQovUmVzb3Vy"
                        "Y2VzIDw8Cj4+Ci9NZWRpYUJveCBbIDAuMCAwLjAgNzIgNzIgXQovUGFyZW50IDIg"
                        "MCBSCj4+CmVuZG9iagp4cmVmCjAgNQowMDAwMDAwMDAwIDY1NTM1IGYgCjAwMDAw"
                        "MDAwMTUgMDAwMDAgbiAKMDAwMDAwMDA1NCAwMDAwMCBuIAowMDAwMDAwMTEzIDAw"
                        "MDAwIG4gCjAwMDAwMDAxNjIgMDAwMDAgbiAKdHJhaWxlcgo8PAovU2l6ZSA1Ci9S"
                        "b290IDMgMCBSCi9JbmZvIDEgMCBSCj4+CnN0YXJ0eHJlZgoyNTQKJSVFT0YK"
                    ),
                ),
                EmailAttachment(
                    "a2",
                    "notes.docx",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "attachment",
                    data=b"docx",
                ),
                EmailAttachment(
                    "a3",
                    "inventory.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "attachment",
                    data=b"xlsx",
                ),
                EmailAttachment("a4", "details.csv", "text/csv", "attachment", data=b"serial,ABC123\n"),
            ]
            storage = AttachmentStorage(Path(root), {"max_attachment_bytes": 2000})

            stored, errors = storage.save("message/1@example.com", attachments)

            expected_dir = Path(root) / hashlib.sha256(
                b"message/1@example.com"
            ).hexdigest()[:32]
            self.assertEqual(errors, [])
            self.assertEqual([item.attachment_id for item in stored], ["a1", "a2", "a3", "a4"])
            self.assertTrue(all(Path(item.path).is_file() for item in stored))
            self.assertTrue(all(expected_dir in Path(item.path).parents for item in stored))

    def test_saves_archive_as_metadata_only_with_safe_filename(self):
        with tempfile.TemporaryDirectory() as root:
            attachment = EmailAttachment(
                "a1", "..\\unsafe:name.zip", "application/zip", "attachment", data=b"archive"
            )
            storage = AttachmentStorage(Path(root), {"max_attachment_bytes": 100})

            stored, errors = storage.save("message@example.com", [attachment])

            self.assertEqual(errors, [])
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0].extraction_status, "metadata_only")
            self.assertEqual(stored[0].extracted_text, "")
            self.assertNotIn("..", stored[0].filename)
            self.assertNotIn(":", stored[0].filename)
            self.assertIn(Path(root).resolve(), Path(stored[0].path).resolve().parents)

    def test_rejects_attachments_over_size_or_count_limits(self):
        with tempfile.TemporaryDirectory() as root:
            attachments = [
                EmailAttachment("a1", "small.txt", "text/plain", "attachment", data=b"okay"),
                EmailAttachment("a2", "large.txt", "text/plain", "attachment", size_bytes=1, data=b"123456"),
                EmailAttachment("a3", "extra.txt", "text/plain", "attachment", data=b"okay"),
            ]
            storage = AttachmentStorage(
                Path(root), {"max_attachments_per_email": 2, "max_attachment_bytes": 5}
            )

            stored, errors = storage.save("message@example.com", attachments)

            self.assertEqual([item.attachment_id for item in stored], ["a1"])
            self.assertEqual(len(errors), 2)
            self.assertTrue(any("大小限制" in error for error in errors))
            self.assertTrue(any("数量限制" in error for error in errors))

    def test_extraction_failure_still_saves_file_and_records_error(self):
        with tempfile.TemporaryDirectory() as root:
            attachment = EmailAttachment(
                "a1",
                "broken.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "attachment",
                data=b"not-a-valid-docx",
            )
            storage = AttachmentStorage(Path(root), {"max_attachment_bytes": 100})

            stored, errors = storage.save("message@example.com", [attachment])

            self.assertEqual(errors, [])
            self.assertTrue(Path(stored[0].path).is_file())
            self.assertEqual(stored[0].extraction_status, "extraction_failed")
            self.assertTrue(stored[0].extraction_error)

    def test_manifest_sanitizes_nested_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            attachment = EmailAttachment(
                "a1",
                "details.txt",
                "text/plain",
                "attachment",
                data=b"hello",
                metadata={
                    "safe": "ok",
                    "data": b"secret",
                    "nested": {"token": b"secret", "blob": bytearray(b"secret")},
                },
            )
            storage = AttachmentStorage(Path(root), {"max_attachment_bytes": 100})

            stored, _errors = storage.save("message@example.com", [attachment])
            manifest = AttachmentStorage.manifest(stored)

            self.assertEqual(manifest[0]["metadata"]["safe"], "ok")
            self.assertNotIn("data", manifest[0]["metadata"])
            self.assertNotIn("token", manifest[0]["metadata"]["nested"])
            self.assertNotIn("blob", manifest[0]["metadata"]["nested"])

    def test_truncates_extracted_text_and_context_to_configured_limits(self):
        with tempfile.TemporaryDirectory() as root:
            attachment = EmailAttachment(
                "a1", "diagnosis.csv", "text/csv", "attachment", data=b"serial,ABC123\nresult,replace-cable\n"
            )
            storage = AttachmentStorage(
                Path(root), {"max_attachment_bytes": 100, "max_extracted_chars": 12}
            )

            stored, errors = storage.save("message@example.com", [attachment])
            context = format_attachment_context(stored, max_chars=8)

            self.assertEqual(errors, [])
            self.assertEqual(stored[0].extraction_status, "extracted")
            self.assertEqual(stored[0].extracted_text, "serial,ABC12")
            self.assertLessEqual(len(context), 8)
            self.assertEqual(AttachmentStorage.manifest(stored)[0]["attachment_id"], "a1")
            self.assertNotIn("data", AttachmentStorage.manifest(stored)[0])


if __name__ == "__main__":
    unittest.main()
