import sys
import unittest
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email_agent.infrastructure.mail_fetcher import MailFetcher


def base_message() -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = "Device fault"
    message["From"] = "Customer <customer@example.com>"
    message["To"] = "support@example.com"
    message["Message-ID"] = "<msg-1@example.com>"
    return message


class MultimodalMailFetcherTests(unittest.TestCase):
    def test_extracts_image_attachment(self):
        message = base_message()
        message.set_content("The switch status is shown in the attached photo.")
        message.add_attachment(
            b"fake-jpeg",
            maintype="image",
            subtype="jpeg",
            filename="fault.jpg",
        )

        parsed = MailFetcher(
            "imap.example.com", 993, "support@example.com", "secret"
        )._parse_raw_email(message.as_bytes())

        self.assertEqual(len(parsed.media), 1)
        self.assertEqual(parsed.media[0].source, "attachment")
        self.assertEqual(parsed.media[0].filename, "fault.jpg")
        self.assertEqual(parsed.media[0].content_type, "image/jpeg")
        self.assertEqual(parsed.media[0].data, b"fake-jpeg")
        self.assertTrue(parsed.media[0].media_id)

    def test_extracts_cid_inline_image(self):
        message = base_message()
        message.set_content("Please see the inline image.")
        message.add_alternative(
            '<html><body><p>Please see this.</p><img src="cid:photo1"></body></html>',
            subtype="html",
        )
        related = message.get_payload()[1]
        related.add_related(
            b"inline-image",
            maintype="image",
            subtype="png",
            cid="<photo1>",
            filename="inline.png",
        )

        parsed = MailFetcher(
            "imap.example.com", 993, "support@example.com", "secret"
        )._parse_raw_email(message.as_bytes())

        self.assertEqual(len(parsed.media), 1)
        self.assertEqual(parsed.media[0].source, "inline_cid")
        self.assertEqual(parsed.media[0].content_id, "photo1")
        self.assertEqual(parsed.media[0].content_type, "image/png")
        self.assertEqual(parsed.media[0].data, b"inline-image")

    def test_extracts_base64_data_image_from_html(self):
        message = base_message()
        message.set_content(
            '<html><body><p>See this</p><img src="data:image/png;base64,aW1hZ2U="></body></html>',
            subtype="html",
        )

        parsed = MailFetcher(
            "imap.example.com", 993, "support@example.com", "secret"
        )._parse_raw_email(message.as_bytes())

        self.assertEqual(len(parsed.media), 1)
        self.assertEqual(parsed.media[0].source, "inline_data")
        self.assertEqual(parsed.media[0].content_type, "image/png")
        self.assertEqual(parsed.media[0].data, b"image")
        self.assertTrue(parsed.media[0].filename.endswith(".png"))


if __name__ == "__main__":
    unittest.main()
