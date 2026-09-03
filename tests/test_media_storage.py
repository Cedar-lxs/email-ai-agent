import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email_agent.domain.models import EmailMedia
from email_agent.infrastructure.media_storage import MediaStorage


class MediaStorageTests(unittest.TestCase):
    def test_saves_media_with_sanitized_filename_inside_message_directory(self):
        with tempfile.TemporaryDirectory() as root:
            temp_root = Path(root)
            media = EmailMedia(
                "m1", "..\\bad:name.jpg", "image/jpeg", "attachment",
                size_bytes=4, data=b"data",
            )
            storage = MediaStorage(
                temp_root, {"max_media_per_email": 8, "max_image_bytes": 100}
            )

            stored, errors = storage.save("message/1@example.com", [media])

            self.assertEqual(errors, [])
            self.assertEqual(len(stored), 1)
            path = Path(stored[0].path).resolve()
            self.assertTrue(path.is_file())
            self.assertIn(temp_root.resolve(), path.parents)
            self.assertNotIn("..", stored[0].filename)
            self.assertNotIn(":", stored[0].filename)
            self.assertEqual(path.read_bytes(), b"data")

    def test_enforces_size_and_count_limits(self):
        with tempfile.TemporaryDirectory() as root:
            media = [
                EmailMedia("m1", "ok.jpg", "image/jpeg", "attachment", size_bytes=4, data=b"data"),
                EmailMedia("m2", "big.jpg", "image/jpeg", "attachment", size_bytes=6, data=b"123456"),
                EmailMedia("m3", "extra.jpg", "image/jpeg", "attachment", size_bytes=4, data=b"more"),
            ]
            storage = MediaStorage(
                Path(root),
                {"max_media_per_email": 2, "max_image_bytes": 5},
            )

            stored, errors = storage.save("msg@example.com", media)

            self.assertEqual([item.media_id for item in stored], ["m1"])
            self.assertEqual(len(errors), 2)
            self.assertTrue(any("超过图片大小限制" in error for error in errors))
            self.assertTrue(any("超过单封邮件媒体数量限制" in error for error in errors))

    def test_video_decode_failure_keeps_original_and_records_error(self):
        with tempfile.TemporaryDirectory() as root:
            media = EmailMedia(
                "v1", "fault.mp4", "video/mp4", "attachment",
                size_bytes=10, data=b"not-a-video",
            )
            storage = MediaStorage(
                Path(root),
                {"max_media_per_email": 8, "max_video_bytes": 100, "video_frame_count": 3},
            )

            with patch.object(storage, "_extract_video_frames", return_value=([], ["视频关键帧提取失败：无法解码视频"])):
                stored, errors = storage.save("msg@example.com", [media])

            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0].content_type, "video/mp4")
            self.assertEqual(stored[0].metadata.get("kind"), "original")
            self.assertEqual(errors, ["视频关键帧提取失败：无法解码视频"])

    def test_manifest_returns_json_safe_records(self):
        with tempfile.TemporaryDirectory() as root:
            media = EmailMedia(
                "m1", "fault.jpg", "image/jpeg", "inline_cid",
                content_id="photo1", size_bytes=4, data=b"data",
            )
            storage = MediaStorage(Path(root), {"max_image_bytes": 100})
            stored, _errors = storage.save("msg@example.com", [media])

            manifest = storage.manifest(stored)

            self.assertEqual(manifest[0]["media_id"], "m1")
            self.assertEqual(manifest[0]["source"], "inline_cid")
            self.assertEqual(manifest[0]["content_id"], "photo1")
            self.assertNotIn("data", manifest[0])


if __name__ == "__main__":
    unittest.main()
