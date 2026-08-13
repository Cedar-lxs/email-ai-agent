"""
IMAP 模块：连接阿里企业邮箱，拉取未读邮件并解析
"""
import asyncio
import imaplib
import email
import hashlib
from email.header import decode_header
from email.utils import parsedate_to_datetime
import re

from email_agent.domain.models import ParsedEmail


class MailFetcher:
    """阿里企业邮箱 IMAP 客户端"""

    def __init__(self, server: str, port: int, account: str, password: str,
                 timeout: float = 30):
        self.server = server
        self.port = port
        self.account = account
        self.password = password
        self.timeout = max(5.0, float(timeout))
        self._conn = None

    def connect(self):
        """建立 SSL 连接并登录"""
        self._conn = imaplib.IMAP4_SSL(self.server, self.port, timeout=self.timeout)
        self._conn.login(self.account, self.password)

    async def connect_async(self):
        """在线程池中建立 IMAP 连接，避免阻塞事件循环。"""
        await asyncio.to_thread(self.connect)

    def disconnect(self):
        if self._conn:
            try:
                self._conn.logout()
            except Exception:
                pass
            finally:
                self._conn = None

    async def disconnect_async(self):
        await asyncio.to_thread(self.disconnect)

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()

    def fetch_unread(self) -> list[ParsedEmail]:
        """拉取所有未读邮件"""
        self._conn.select("INBOX")
        # IMAP 搜索：UNSEEN = 未读
        status, data = self._conn.search(None, "UNSEEN")

        if status != "OK" or not data[0]:
            return []

        email_ids = data[0].split()
        emails = []

        for eid in email_ids:
            status, msg_data = self._conn.fetch(eid, "(BODY.PEEK[])")
            if status != "OK":
                continue

            raw_email = msg_data[0][1]
            parsed = self._parse_raw_email(raw_email)
            if parsed:
                parsed.imap_uid = eid.decode("ascii", errors="ignore")
                emails.append(parsed)

        return emails

    async def fetch_unread_async(self) -> list[ParsedEmail]:
        """异步拉取未读邮件，不阻塞事件循环。"""
        return await asyncio.to_thread(self.fetch_unread)

    def mark_seen(self, imap_uid: str):
        """仅在邮件成功落库、生成草稿或完成转人工后标记已读。"""
        if self._conn and imap_uid:
            self._conn.store(imap_uid.encode("ascii"), "+FLAGS", "\\Seen")

    async def mark_seen_async(self, imap_uid: str):
        await asyncio.to_thread(self.mark_seen, imap_uid)

    def _parse_raw_email(self, raw_bytes: bytes) -> ParsedEmail:
        """解析原始邮件字节流"""
        msg = email.message_from_bytes(raw_bytes)

        # --- 主题 ---
        subject = self._decode_mime_str(msg.get("Subject", "(无主题)"))

        # --- 发件人 ---
        sender_name, sender_addr = self._parse_sender(msg.get("From", ""))

        # --- Message-ID ---
        message_id = msg.get("Message-ID", "").strip("<>")
        if not message_id:
            digest = hashlib.sha256(raw_bytes).hexdigest()[:32]
            message_id = f"generated-{digest}@local"

        # --- 接收时间 ---
        date_str = msg.get("Date", "")
        try:
            received_at = parsedate_to_datetime(date_str).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            received_at = date_str

        # --- References / In-Reply-To ---
        in_reply_to = msg.get("In-Reply-To", "").strip("<>")

        # --- 正文 ---
        body_text = ""
        body_html = ""

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                disposition = str(part.get("Content-Disposition", ""))

                # 跳过附件
                if "attachment" in disposition:
                    continue

                charset = part.get_content_charset() or "utf-8"
                payload = part.get_payload(decode=True)

                if not payload:
                    continue

                try:
                    decoded = payload.decode(charset, errors="replace")
                except (LookupError, UnicodeDecodeError):
                    decoded = payload.decode("utf-8", errors="replace")

                if content_type == "text/plain" and not body_text:
                    body_text = decoded
                elif content_type == "text/html" and not body_html:
                    body_html = decoded
        else:
            charset = msg.get_content_charset() or "utf-8"
            payload = msg.get_payload(decode=True)
            if payload:
                body_text = payload.decode(charset, errors="replace")

        # 如果没有纯文本，从 HTML 中提取
        if not body_text and body_html:
            body_text = self._strip_html(body_html)

        return ParsedEmail(
            message_id=message_id,
            subject=subject,
            sender=sender_addr,
            sender_name=sender_name,
            body_text=body_text.strip(),
            body_html=body_html,
            received_at=received_at,
            in_reply_to=in_reply_to,
        )

    # ============================================================
    # 工具方法
    # ============================================================

    def _decode_mime_str(self, raw: str) -> str:
        """解码 MIME 编码的字符串（如 =?UTF-8?B?xxx?=）"""
        parts = decode_header(raw)
        result = []
        for text, charset in parts:
            if isinstance(text, bytes):
                result.append(text.decode(charset or "utf-8", errors="replace"))
            else:
                result.append(text)
        return "".join(result)

    def _parse_sender(self, from_header: str) -> tuple:
        """解析发件人：返回 (显示名, 邮箱地址)"""
        # 格式通常是：周周 <zhouzhou@example.com>
        match = re.match(r'(.*?)\s*<?([\w.+-]+@[\w-]+\.[\w.]+)>?', from_header)
        if match:
            name = self._decode_mime_str(match.group(1).strip().strip('"'))
            addr = match.group(2)
            return name, addr
        return from_header, from_header

    def _strip_html(self, html: str) -> str:
        """简单去除 HTML 标签，获取纯文本"""
        # 先处理换行标签
        html = re.sub(r'<br\s*/?>', '\n', html, flags=re.I)
        html = re.sub(r'</?p[^>]*>', '\n', html, flags=re.I)
        # 去掉其余标签
        html = re.sub(r'<[^>]+>', '', html)
        # 解码 HTML 实体
        import html as html_mod
        return html_mod.unescape(html).strip()
