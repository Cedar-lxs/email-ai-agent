import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email_agent.application.review_service import ReviewService
from email_agent.infrastructure.database import EmailDB
from email_agent.infrastructure.mail_sender import MailSender


class FakeSender(MailSender):
    def __init__(self, succeed=True):
        super().__init__("test", 465, "agent@test", "secret")
        self.succeed = succeed

    def send_reply(self, *args, **kwargs):
        return self.succeed


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = EmailDB(str(root / "test.db"))
        self.sender = FakeSender()
        self.service = ReviewService(self.db, self.sender, root / "drafts")
        self.db.mark_processed("m1", "主题", "a@test", status="draft_ready", original_body="问题")
        path = self.sender.save_draft("a@test", "主题", "草稿", str(root / "drafts"), "m1")
        self.db.update_status("m1", "draft_ready", "草稿", path)

    def tearDown(self):
        self.db.conn.close()
        self.temp.cleanup()

    def test_edit_approve_and_delete(self):
        self.service.edit("m1", "人工修改")
        self.assertEqual(self.db.get_email("m1")["draft_text"], "人工修改")
        self.service.approve("m1")
        self.assertEqual(self.db.get_email("m1")["status"], "replied")
        self.assertEqual(self.service.delete(["m1"]), ["m1"])

    def test_send_failure_keeps_draft(self):
        self.service.sender.succeed = False
        with self.assertRaises(RuntimeError):
            self.service.approve("m1")
        self.assertEqual(self.db.get_email("m1")["status"], "draft_ready")


if __name__ == "__main__":
    unittest.main()
