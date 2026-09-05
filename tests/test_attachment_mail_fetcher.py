import sys
import unittest
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email_agent.infrastructure.mail_fetcher import MailFetcher


def base_message() -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = "Attachment test"
    message["From"] = "Customer <customer@example.com>"
    message["To"] = "support@example.com"
    message["Message-ID"] = "<attachment-test@example.com>"
    return message


def parse(message: EmailMessage):
    return MailFetcher(
        "imap.example.com", 993, "support@example.com", "secret"
    )._parse_raw_email(message.as_bytes())


class AttachmentMailFetcherTests(unittest.TestCase):
    def test_extracts_pdf_as_ordinary_attachment(self):
        message = base_message()
        message.set_content("Please review the service report.")
        message.add_attachment(
            b"%PDF-ordinary-attachment",
            maintype="application",
            subtype="pdf",
            filename="service-report.pdf",
        )

        parsed = parse(message)

        self.assertEqual(len(parsed.attachments), 1)
        attachment = parsed.attachments[0]
        self.assertEqual(attachment.filename, "service-report.pdf")
        self.assertEqual(attachment.content_type, "application/pdf")
        self.assertEqual(attachment.source, "attachment")
        self.assertEqual(attachment.data, b"%PDF-ordinary-attachment")
        self.assertEqual(attachment.size_bytes, 24)
        self.assertTrue(attachment.attachment_id)
        self.assertEqual(parsed.media, [])

    def test_extracts_docx_as_ordinary_attachment(self):
        message = base_message()
        message.set_content("The requested document is attached.")
        message.add_attachment(
            b"docx-content",
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename="troubleshooting.docx",
        )

        parsed = parse(message)

        self.assertEqual(len(parsed.attachments), 1)
        attachment = parsed.attachments[0]
        self.assertEqual(attachment.filename, "troubleshooting.docx")
        self.assertEqual(
            attachment.content_type,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.assertEqual(attachment.data, b"docx-content")

    def test_separates_image_media_from_pdf_attachment(self):
        message = base_message()
        message.set_content("Photo and report are attached.")
        message.add_attachment(
            b"image-content",
            maintype="image",
            subtype="jpeg",
            filename="fault.jpg",
        )
        message.add_attachment(
            b"pdf-content",
            maintype="application",
            subtype="pdf",
            filename="fault-report.pdf",
        )

        parsed = parse(message)

        self.assertEqual(len(parsed.media), 1)
        self.assertEqual(parsed.media[0].filename, "fault.jpg")
        self.assertEqual(parsed.media[0].source, "attachment")
        self.assertEqual(parsed.media[0].data, b"image-content")
        self.assertEqual(len(parsed.attachments), 1)
        self.assertEqual(parsed.attachments[0].filename, "fault-report.pdf")
        self.assertEqual(parsed.attachments[0].source, "attachment")
        self.assertEqual(parsed.attachments[0].data, b"pdf-content")


if __name__ == "__main__":
    unittest.main()
