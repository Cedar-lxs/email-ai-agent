"""
SMTP 模块：通过阿里企业邮箱发送回复
"""
import smtplib
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
from datetime import datetime


class MailSender:
    """阿里企业邮箱 SMTP 客户端"""

    def __init__(self, server: str, port: int, account: str, password: str,
                 sender_name: str = "AI售后助手"):
        self.server = server
        self.port = port
        self.account = account
        self.password = password
        self.sender_name = sender_name

    def send_reply(self, to_address: str, subject: str, body: str,
                   in_reply_to: str = "", format_type: str = "plain") -> bool:
        """
        发送回复邮件

        参数:
            to_address: 收件人地址
            subject: 邮件主题（会自动加 Re:）
            body: 邮件正文
            in_reply_to: 原邮件 Message-ID（用于邮件线程关联）
            format_type: "plain" 纯文本 或 "html"

        返回: 成功 True / 失败 False
        """
        reply_subject = self.build_reply_subject(subject)

        if format_type == "html":
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(self._strip_html(body), "plain", "utf-8"))
            msg.attach(MIMEText(body, "html", "utf-8"))
        else:
            msg = MIMEText(body, "plain", "utf-8")

        msg["From"] = formataddr((self.sender_name, self.account))
        msg["To"] = to_address
        msg["Subject"] = reply_subject
        msg["Date"] = datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0800")

        # 邮件线程关联：让回复出现在正确的对话线程里
        if in_reply_to:
            msg["In-Reply-To"] = f"<{in_reply_to}>"
            msg["References"] = f"<{in_reply_to}>"

        try:
            with smtplib.SMTP_SSL(self.server, self.port) as smtp:
                smtp.login(self.account, self.password)
                smtp.send_message(msg)
            return True
        except Exception as e:
            print(f"[SMTP Error] 发送失败: {e}")
            return False

    def save_draft(self, to_address: str, subject: str, body: str,
                   draft_dir: str = "./drafts", message_id: str = "",
                   filepath: str = "") -> str:
        """保存结构化待审核草稿；可使用固定路径覆盖人工编辑版本。"""
        from pathlib import Path
        Path(draft_dir).mkdir(parents=True, exist_ok=True)

        if filepath:
            target = Path(filepath)
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            safe_address = re.sub(r"[^a-zA-Z0-9_.-]", "_", to_address.replace("@", "_at_"))
            target = Path(draft_dir) / f"{timestamp}_{safe_address}.txt"

        reply_subject = self.build_reply_subject(subject)
        content = f"""Message-ID: {message_id}
收件人: {to_address}
主题: {reply_subject}
生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
状态: 待审核
---
{body}
---
[此草稿由 AI 生成，请审核后发送]
"""
        target.write_text(content, encoding="utf-8")
        return str(target)

    @staticmethod
    def read_draft_body(filepath) -> str:
        """读取草稿分隔线之间的人工可编辑正文。"""
        text = filepath.read_text(encoding="utf-8")
        parts = text.split("\n---\n")
        if len(parts) < 3:
            raise ValueError(f"草稿格式无效: {filepath}")
        return parts[1].strip()

    @staticmethod
    def build_reply_subject(original_subject: str) -> str:
        """根据客户原主题生成回复主题，避免重复添加回复前缀。"""
        subject = re.sub(r"[\r\n]+", " ", str(original_subject or "")).strip()
        if not subject or subject == "(无主题)":
            subject = "客户技术问题"

        reply_prefix = re.compile(
            r"^(?:re|回复|答复|回覆)\s*[:：]\s*", re.IGNORECASE
        )
        if reply_prefix.match(subject):
            subject = reply_prefix.sub("", subject, count=1).strip() or "客户技术问题"
        return f"Re: {subject}"

    @staticmethod
    def _strip_html(html: str) -> str:
        import re
        html = re.sub(r'<br\s*/?>', '\n', html, flags=re.I)
        html = re.sub(r'</?p[^>]*>', '\n', html, flags=re.I)
        html = re.sub(r'<[^>]+>', '', html)
        import html as html_mod
        return html_mod.unescape(html).strip()
