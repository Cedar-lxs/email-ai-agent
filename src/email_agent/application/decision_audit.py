"""Build concise, safe records of email processing decisions."""

_BUSINESS_INTENT_TERMS = (
    "business", "order", "quote", "price", "refund", "return", "warranty",
    "业务", "订单", "报价", "价格", "退款", "退货", "保修", "投诉",
)
_ACTIONS = {"auto_sent", "draft_ready", "escalated", "failed", "skipped_self", "unknown"}
_MODES = {"semi_auto", "full_auto", "unknown"}
_SENTIMENTS = {"neutral", "positive", "angry", "negative", "unknown"}
_URGENCIES = {"low", "medium", "high", "unknown"}
_BLOCKING_REASONS = {
    "self_message",
    "business_or_high_risk",
    "visual_risk",
    "translation_failed",
    "missing_knowledge",
    "reply_generation_failed",
    "processing_failure",
    "semi_auto_mode",
    "intent_not_auto_allowed",
    "media_processing_error",
    "web_search_auto_send_disabled",
    "attachment_processing_error",
    "product_conflict",
    "unknown",
}
_SIGNALS = (
    "visual_risk",
    "web_search_fallback",
    "media_present",
    "attachments_present",
    "attachment_text_present",
    "product_context_present",
    "product_conflict",
    "unknown",
)


def _enum(value: str, allowed: set[str], fallback: str = "unknown") -> str:
    text = str(value or "").strip()
    return text if text in allowed else fallback


def _signal(value: str) -> str:
    text = str(value or "").strip()
    return text if text in _SIGNALS else "unknown"


def build_decision_trace(
    *,
    action: str,
    mode: str,
    intent: str,
    sentiment: str,
    urgency: str,
    auto_allowed_intent: bool,
    local_knowledge_hits: int,
    used_web_search: bool,
    media_count: int,
    attachment_count: int,
    known_intents: set[str] | list[str] | tuple[str, ...] | None = None,
    blocking_reasons: list[str] | None = None,
    signals: list[str] | None = None,
) -> dict:
    """Return a compact decision trace from explicit, non-sensitive inputs."""
    action = _enum(action, _ACTIONS)
    mode = _enum(mode, _MODES)
    known_intent_set = set(known_intents or [])
    known_intent_set.add("unknown")
    intent = str(intent or "unknown").strip()
    if intent not in known_intent_set:
        intent = "unknown"
    sentiment = _enum(sentiment, _SENTIMENTS)
    urgency = _enum(urgency, _URGENCIES)
    reasons = list(blocking_reasons or [])
    if action == "draft_ready" and mode == "semi_auto":
        reasons.append("semi_auto_mode")
    if action == "escalated" and (urgency == "high" or sentiment == "angry"):
        reasons.append("business_or_high_risk")
    if action == "escalated" and any(term in intent.lower() for term in _BUSINESS_INTENT_TERMS):
        reasons.append("business_or_high_risk")

    return {
        "action": action,
        "mode": mode,
        "intent": intent,
        "sentiment": sentiment,
        "urgency": urgency,
        "auto_allowed_intent": auto_allowed_intent,
        "local_knowledge_hits": local_knowledge_hits,
        "used_web_search": used_web_search,
        "media_count": media_count,
        "attachment_count": attachment_count,
        "blocking_reasons": list(dict.fromkeys(_enum(reason, _BLOCKING_REASONS) for reason in reasons)),
        "signals": list(dict.fromkeys(_signal(signal) for signal in (signals or []))),
    }
