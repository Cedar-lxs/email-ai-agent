import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email_agent.domain.models import StoredMedia
from email_agent.infrastructure.multimodal import (
    MultimodalAnalyzer,
    format_multimodal_context,
)


class MultimodalAnalyzerTests(unittest.TestCase):
    def stored_image(self, root: Path) -> StoredMedia:
        path = root / "fault.jpg"
        path.write_bytes(b"fake-image")
        return StoredMedia(
            media_id="m1",
            filename="fault.jpg",
            content_type="image/jpeg",
            source="attachment",
            path=str(path),
            size_bytes=10,
            metadata={"kind": "original"},
        )

    def response(self, content: str):
        return SimpleNamespace(
            raise_for_status=Mock(),
            json=Mock(return_value={"choices": [{"message": {"content": content}}]}),
        )

    def test_request_payload_uses_openai_compatible_image_blocks(self):
        with tempfile.TemporaryDirectory() as root:
            image = self.stored_image(Path(root))
            analyzer = MultimodalAnalyzer({
                "ai": {"api_key": "text-key"},
                "multimodal": {
                    "enabled": True,
                    "api_key": "vision-key",
                    "api_base": "https://api.deepseek.com",
                    "model": "deepseek-v4-flash-vision-exp",
                },
            })
            payload = '{"summary":"Port light is off","visible_text":["GS105"],"product_identifiers":["GS105"],"fault_signals":["port light off"],"risk_signals":[],"confidence":0.8}'

            with patch(
                "email_agent.infrastructure.multimodal.httpx.post",
                return_value=self.response(payload),
            ) as post:
                analyzer.analyze("Port issue", "The port is not working", [image])

            url = post.call_args.args[0]
            body = post.call_args.kwargs["json"]
            content = body["messages"][1]["content"]
            self.assertEqual(url, "https://api.deepseek.com/v1/chat/completions")
            self.assertEqual(body["model"], "deepseek-v4-flash-vision-exp")
            self.assertEqual(content[0]["type"], "text")
            self.assertEqual(content[1]["type"], "image_url")
            self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_normalizes_json_response(self):
        with tempfile.TemporaryDirectory() as root:
            image = self.stored_image(Path(root))
            analyzer = MultimodalAnalyzer({
                "ai": {"api_key": "text-key"},
                "multimodal": {"enabled": True, "api_key": "vision-key"},
            })
            payload = '{"summary":"The label shows GS105.","visible_text":"GS105","product_identifiers":"GS105","fault_signals":["offline"],"connection_state":"connected","indicator_state":"off","risk_signals":[],"needed_information":"firmware version","confidence":1.5}'

            with patch(
                "email_agent.infrastructure.multimodal.httpx.post",
                return_value=self.response(payload),
            ):
                observation = analyzer.analyze("Offline", "Device offline", [image])

            self.assertEqual(observation.summary, "The label shows GS105.")
            self.assertEqual(observation.visible_text, ["GS105"])
            self.assertEqual(observation.product_identifiers, ["GS105"])
            self.assertEqual(observation.connection_state, ["connected"])
            self.assertEqual(observation.indicator_state, ["off"])
            self.assertEqual(observation.needed_information, ["firmware version"])
            self.assertEqual(observation.confidence, 1.0)
            self.assertEqual(observation.errors, [])

    def test_api_failure_returns_observation_error(self):
        with tempfile.TemporaryDirectory() as root:
            image = self.stored_image(Path(root))
            analyzer = MultimodalAnalyzer({
                "ai": {"api_key": "text-key"},
                "multimodal": {"enabled": True, "api_key": "vision-key"},
            })

            with patch(
                "email_agent.infrastructure.multimodal.httpx.post",
                side_effect=RuntimeError("network down"),
            ):
                observation = analyzer.analyze("Offline", "Device offline", [image])

            self.assertEqual(observation.summary, "")
            self.assertTrue(any("network down" in error for error in observation.errors))

    def test_async_analyze_uses_same_normalization(self):
        with tempfile.TemporaryDirectory() as root:
            image = self.stored_image(Path(root))
            analyzer = MultimodalAnalyzer({
                "ai": {"api_key": "text-key"},
                "multimodal": {"enabled": True, "api_key": "vision-key"},
            })
            payload = '{"summary":"Image is blurry.","visible_text":[],"product_identifiers":[],"fault_signals":[],"risk_signals":["blurry"],"confidence":0.4}'

            client = AsyncMock()
            client.__aenter__.return_value = client
            client.post.return_value = self.response(payload)
            with patch("email_agent.infrastructure.multimodal.httpx.AsyncClient", return_value=client):
                observation = asyncio.run(analyzer.analyze_async("Image", "See photo", [image]))

            self.assertEqual(observation.summary, "Image is blurry.")
            self.assertEqual(observation.risk_signals, ["blurry"])
            self.assertEqual(observation.confidence, 0.4)

    def test_formats_multimodal_context(self):
        context = format_multimodal_context(
            SimpleNamespace(
                summary="Port LED appears off.",
                visible_text=["GS105"],
                product_identifiers=["GS105"],
                fault_signals=["port LED off"],
                connection_state=[],
                indicator_state=["off"],
                risk_signals=[],
                needed_information=["exact power adapter"],
                confidence=0.82,
                errors=[],
            )
        )

        self.assertIn("[Visual observation]", context)
        self.assertIn("GS105", context)
        self.assertIn("0.82", context)


if __name__ == "__main__":
    unittest.main()
