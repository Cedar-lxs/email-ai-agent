"""Persist outbound replies before crossing the SMTP send boundary."""
import hashlib
import uuid

from email_agent.domain.mail_jobs import DeliveryStatus
from email_agent.infrastructure.mail_sender import SmtpOutcome


class DeliveryUncertainError(RuntimeError):
    pass


class DeliveryService:
    def __init__(self, store, sender, smtp_timeout: float = 30,
                 send_lease_seconds: int = 600):
        self.store = store
        self.sender = sender
        self.smtp_timeout = float(smtp_timeout)
        self.send_lease_seconds = int(send_lease_seconds)

    def _message_id(self) -> str:
        domain = str(getattr(self.sender, "account", "") or "local").partition("@")[2]
        return f"<ea-{uuid.uuid4().hex}@{domain or 'local'}>"

    def prepare_reply(self, *, job_id, business_message_id, recipient, subject, body,
                      in_reply_to, created_by, supersedes_id=None):
        active = self.store.get_active_delivery_for_message(business_message_id)
        if active and active["status"] == DeliveryStatus.UNCERTAIN.value:
            raise ValueError("该邮件存在待确认投递，必须先人工确认")
        if active:
            raise ValueError("该邮件已经存在活动投递记录")
        return self.store.create_delivery(
            job_id=job_id,
            business_message_id=business_message_id,
            email_message_id=self._message_id(),
            recipient=recipient,
            subject=subject,
            body=body,
            in_reply_to=in_reply_to,
            body_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
            created_by=created_by,
            supersedes_id=supersedes_id,
        )

    def send_prepared(self, delivery_id: str):
        delivery = self.store.require_delivery(delivery_id, DeliveryStatus.PREPARED)
        message = self.sender.build_reply_message(
            delivery["recipient"], delivery["subject"], delivery["body"],
            delivery["in_reply_to"], delivery["email_message_id"],
        )
        result = self.sender.deliver_message(
            message,
            timeout=self.smtp_timeout,
            before_send=lambda: self.store.mark_delivery_sending(
                delivery_id, self.send_lease_seconds
            ),
        )
        if result.outcome == SmtpOutcome.ACCEPTED:
            self.store.accept_delivery(delivery_id, result.detail)
        elif result.outcome == SmtpOutcome.SAFE_FAILURE:
            self.store.fail_delivery_safely(delivery_id, result.detail)
        else:
            self.store.mark_delivery_uncertain(delivery_id, result.detail)
        return result

    def confirm_sent(self, delivery_id: str, actor: str, reason: str):
        if not str(actor).strip() or not str(reason).strip():
            raise ValueError("确认发送必须填写操作者和原因")
        self.store.require_delivery(delivery_id, DeliveryStatus.UNCERTAIN)
        self.store.accept_delivery(
            delivery_id, f"人工确认: {reason}", actor=actor, reason=reason
        )
        return self.store.get_delivery(delivery_id)

    def authorize_resend(self, delivery_id: str, actor: str, reason: str):
        if not str(actor).strip() or not str(reason).strip():
            raise ValueError("授权重发必须填写操作者和原因")
        original = self.store.require_delivery(delivery_id, DeliveryStatus.UNCERTAIN)
        self.store.cancel_uncertain_delivery(delivery_id, actor, reason)
        replacement_id = self.prepare_reply(
            job_id=original["job_id"],
            business_message_id=original["business_message_id"],
            recipient=original["recipient"],
            subject=original["subject"],
            body=original["body"],
            in_reply_to=original["in_reply_to"],
            created_by=actor,
            supersedes_id=delivery_id,
        )
        return replacement_id, self.send_prepared(replacement_id)
