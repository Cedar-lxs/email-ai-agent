"""DeepSeek vision-based multimodal analysis for email media."""
import base64
import os
from pathlib import Path

import httpx

from email_agent.domain.models import MultimodalObservation, StoredMedia
from email_agent.infrastructure.llm import AIProcessor


MULTIMODAL_PROMPT = """You are a technical after-sales vision analyst.
Analyze the customer email and attached/inline images. The images may show
network switches, PoE equipment, ports, LEDs, labels, wiring, screenshots, or
physical damage.

Rules:
1. Only describe what is visible or strongly implied by the images.
2. Do not invent product specifications, compatibility, repair steps, or causes.
3. Treat blurry or incomplete images as low confidence.
4. Flag any safety or high-risk signals, including smoke, burnt marks, water
   damage, exposed wiring, disassembly, unsafe power cabling, firmware flashing,
   factory reset, data deletion, voltage changes, or PoE power modification.
5. Return one JSON object only, with these keys:
   summary, visible_text, product_identifiers, fault_signals, connection_state,
   indicator_state, risk_signals, needed_information, confidence.

Email subject: {subject}
Email body:
{body}
"""


def format_multimodal_context(observation: MultimodalObservation | None) -> str:
    if not observation:
        return ""
    parts = [
        "[Visual observation]",
        f"Summary: {observation.summary or '(none)'}",
        f"Visible text: {', '.join(observation.visible_text) or '(none)'}",
        f"Product identifiers: {', '.join(observation.product_identifiers) or '(none)'}",
        f"Fault signals: {', '.join(observation.fault_signals) or '(none)'}",
        f"Connection state: {', '.join(observation.connection_state) or '(none)'}",
        f"Indicator state: {', '.join(observation.indicator_state) or '(none)'}",
        f"Risk signals: {', '.join(observation.risk_signals) or '(none)'}",
        f"Needed information: {', '.join(observation.needed_information) or '(none)'}",
        f"Confidence: {observation.confidence:.2f}",
    ]
    if observation.errors:
        parts.append(f"Analysis errors: {', '.join(observation.errors)}")
    return "\n".join(parts)


class MultimodalAnalyzer:
    IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

    def __init__(self, config: dict):
        multimodal = config.get("multimodal", {})
        ai = config.get("ai", {})
        self.enabled = bool(multimodal.get("enabled", False))
        self.api_key = (
            os.getenv("MULTIMODAL_API_KEY", "").strip()
            or str(multimodal.get("api_key", "")).strip()
            or str(ai.get("api_key", "")).strip()
        )
        self.api_base = str(
            multimodal.get("api_base") or ai.get("api_base") or "https://api.deepseek.com"
        ).rstrip("/")
        self.model = str(multimodal.get("model", "deepseek-v4-flash-vision-exp"))
        self.timeout = float(multimodal.get("analysis_timeout", 90))
        self.max_images = int(multimodal.get("max_images_per_request", multimodal.get("max_media_per_email", 8)))

    @property
    def available(self) -> bool:
        return self.enabled and bool(self.api_key)

    def analyze(self, subject: str, body: str,
                media: list[StoredMedia]) -> MultimodalObservation:
        images = self._image_media(media)
        if not images:
            return MultimodalObservation()
        if not self.available:
            return MultimodalObservation(errors=["多模态分析未启用或缺少 API Key"])
        try:
            response = httpx.post(
                f"{self.api_base}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=self._payload(subject, body, images),
                timeout=self.timeout,
            )
            response.raise_for_status()
            return self._observation_from_response(response.json(), images)
        except Exception as exc:
            return MultimodalObservation(errors=[f"多模态分析失败：{exc}"])

    async def analyze_async(self, subject: str, body: str,
                            media: list[StoredMedia]) -> MultimodalObservation:
        images = self._image_media(media)
        if not images:
            return MultimodalObservation()
        if not self.available:
            return MultimodalObservation(errors=["多模态分析未启用或缺少 API Key"])
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_base}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=self._payload(subject, body, images),
                    timeout=self.timeout,
                )
            response.raise_for_status()
            return self._observation_from_response(response.json(), images)
        except Exception as exc:
            return MultimodalObservation(errors=[f"多模态分析失败：{exc}"])

    def _payload(self, subject: str, body: str, images: list[StoredMedia]) -> dict:
        content = [{
            "type": "text",
            "text": MULTIMODAL_PROMPT.format(subject=subject[:500], body=body[:3000]),
        }]
        for item in images[:self.max_images]:
            content.append({
                "type": "image_url",
                "image_url": {"url": self._data_url(item)},
            })
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You analyze technical support images and return strict JSON."},
                {"role": "user", "content": content},
            ],
            "temperature": 0.1,
            "max_tokens": 1200,
        }

    def _observation_from_response(self, payload: dict,
                                   images: list[StoredMedia]) -> MultimodalObservation:
        content = payload["choices"][0]["message"].get("content") or "{}"
        data = AIProcessor._parse_json_object(content)
        return MultimodalObservation(
            summary=str(data.get("summary", "")).strip(),
            visible_text=self._list(data.get("visible_text")),
            product_identifiers=self._list(data.get("product_identifiers")),
            fault_signals=self._list(data.get("fault_signals")),
            connection_state=self._list(data.get("connection_state")),
            indicator_state=self._list(data.get("indicator_state")),
            risk_signals=self._list(data.get("risk_signals")),
            needed_information=self._list(data.get("needed_information")),
            confidence=self._confidence(data.get("confidence", 0)),
            raw_items=[{"media_id": item.media_id, "filename": item.filename,
                        "source": item.source, "derived_from": item.derived_from}
                       for item in images],
        )

    def _image_media(self, media: list[StoredMedia]) -> list[StoredMedia]:
        return [item for item in media if item.content_type in self.IMAGE_TYPES and Path(item.path).is_file()]

    @staticmethod
    def _data_url(item: StoredMedia) -> str:
        data = base64.b64encode(Path(item.path).read_bytes()).decode("ascii")
        return f"data:{item.content_type};base64,{data}"

    @staticmethod
    def _list(value) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            items = value
        else:
            items = [value]
        return [str(item).strip() for item in items if str(item).strip()]

    @staticmethod
    def _confidence(value) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, number))
