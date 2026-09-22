"""Durable inbound job and outbound delivery states."""
from dataclasses import dataclass
from enum import Enum


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    RETRY_WAIT = "retry_wait"
    SEND_PREPARED = "send_prepared"
    SENDING = "sending"
    DRAFT_READY = "draft_ready"
    ESCALATED = "escalated"
    SENT = "sent"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    DEAD_LETTER = "dead_letter"


class DeliveryStatus(str, Enum):
    PREPARED = "prepared"
    SENDING = "sending"
    ACCEPTED = "accepted"
    UNCERTAIN = "uncertain"
    FAILED_SAFE = "failed_safe"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class MailboxCursor:
    account: str
    folder: str
    uid_validity: str
    last_scanned_uid: int


TERMINAL_JOB_STATUSES = {
    JobStatus.DRAFT_READY,
    JobStatus.ESCALATED,
    JobStatus.SENT,
    JobStatus.DEAD_LETTER,
}

JOB_TRANSITIONS = {
    JobStatus.PENDING: {JobStatus.PROCESSING},
    JobStatus.RETRY_WAIT: {JobStatus.PROCESSING, JobStatus.ESCALATED},
    JobStatus.PROCESSING: {
        JobStatus.RETRY_WAIT,
        JobStatus.SEND_PREPARED,
        JobStatus.DRAFT_READY,
        JobStatus.ESCALATED,
        JobStatus.DEAD_LETTER,
    },
    JobStatus.SEND_PREPARED: {
        JobStatus.SENDING,
        JobStatus.RETRY_WAIT,
        JobStatus.DEAD_LETTER,
        JobStatus.AWAITING_CONFIRMATION,
    },
    JobStatus.SENDING: {JobStatus.SENT, JobStatus.AWAITING_CONFIRMATION},
    JobStatus.AWAITING_CONFIRMATION: {
        JobStatus.SEND_PREPARED,
        JobStatus.SENT,
        JobStatus.ESCALATED,
    },
    JobStatus.DEAD_LETTER: {JobStatus.PENDING, JobStatus.ESCALATED},
}

DELIVERY_TRANSITIONS = {
    DeliveryStatus.PREPARED: {
        DeliveryStatus.SENDING,
        DeliveryStatus.FAILED_SAFE,
        DeliveryStatus.UNCERTAIN,
        DeliveryStatus.CANCELLED,
    },
    DeliveryStatus.SENDING: {
        DeliveryStatus.ACCEPTED,
        DeliveryStatus.UNCERTAIN,
    },
    DeliveryStatus.UNCERTAIN: {
        DeliveryStatus.ACCEPTED,
        DeliveryStatus.CANCELLED,
    },
}


def require_transition(current, target, allowed) -> None:
    current_value = current.value if isinstance(current, Enum) else str(current)
    target_value = target.value if isinstance(target, Enum) else str(target)
    allowed_values = {
        item.value if isinstance(item, Enum) else str(item)
        for item in allowed.get(current_value, allowed.get(current, set()))
    }
    if target_value not in allowed_values:
        raise ValueError(f"非法状态转换: {current_value} -> {target_value}")
