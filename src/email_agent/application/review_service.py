"""草稿人工审核应用服务。"""
from pathlib import Path

from email_agent.infrastructure.mail_sender import MailSender


class ReviewService:
    def __init__(self, db, sender, draft_dir):
        self.db = db
        self.sender = sender
        self.draft_dir = str(draft_dir)

    def require_draft(self, message_id: str):
        row = self.db.get_email(message_id)
        if not row:
            raise ValueError(f"找不到邮件: {message_id}")
        if row["status"] != "draft_ready":
            raise ValueError(f"邮件状态不是 draft_ready，而是 {row['status']}")
        return row

    @staticmethod
    def draft_body(row) -> str:
        path = Path(row["draft_path"] or "")
        if path.is_file():
            return MailSender.read_draft_body(path)
        return row["draft_text"] or ""

    def edit(self, message_id: str, body: str):
        row = self.require_draft(message_id)
        body = body.strip()
        if not body or len(body) > 50000:
            raise ValueError("回复正文不能为空且不能超过 50000 字符")
        path = self.sender.save_draft(
            row["sender"], row["subject"], body, self.draft_dir,
            message_id=message_id, filepath=row["draft_path"] or "",
        )
        self.db.update_status(message_id, "draft_ready", body, path, "人工已编辑")
        return path

    def approve(self, message_id: str):
        row = self.require_draft(message_id)
        body = self.draft_body(row)
        if not self.sender.send_reply(row["sender"], row["subject"], body, message_id):
            self.db.record_error(message_id, "人工批准后 SMTP 发送失败", keep_status=True)
            raise RuntimeError("发送失败，草稿保持待审核状态，可稍后重试")
        self.db.update_status(message_id, "replied", body, notes="人工审核批准")

    def reject(self, message_id: str, reason: str = "人工拒绝"):
        self.require_draft(message_id)
        self.db.update_status(message_id, "rejected", notes=reason)

    def delete(self, message_ids: list[str]) -> list[str]:
        return self.db.delete_emails(message_ids)
