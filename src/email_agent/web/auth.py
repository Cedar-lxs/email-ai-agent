"""Persistent authentication, session, and request-protection helpers."""
import base64
import functools
import hashlib
import math
import os
import re
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from flask import current_app, jsonify, request


@dataclass(frozen=True)
class AuthIdentity:
    username: str
    source: str
    token: str = ""
    csrf_hash: str = ""


class AuthManager:
    """Manage the single local administrator and persisted sessions."""

    COOKIE_NAME = "email_agent_session"
    CSRF_COOKIE_NAME = "email_agent_csrf"
    SESSION_HOURS = 12
    MAX_FAILED_LOGINS = 5
    FAILURE_WINDOW_MINUTES = 15
    LOCK_MINUTES = 15
    _USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")
    _DUMMY_HASH = "scrypt$16384$8$1$MDAwMDAwMDAwMDAwMDAwMA==$S-kZ5BqLNYLnw2lyoX8sKNGI2aw8kkPhCB6zyo7RqGc="

    def __init__(self, db):
        self.db = db
        self.conn = db.conn
        self._lock = threading.RLock()
        self._init_tables()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii")

    @staticmethod
    def _decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value.encode("ascii"))

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _init_tables(self):
        with self._lock:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS auth_users (
                    username TEXT PRIMARY KEY COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    username TEXT NOT NULL COLLATE NOCASE,
                    csrf_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_auth_sessions_username
                    ON auth_sessions(username);
                CREATE TABLE IF NOT EXISTS auth_login_attempts (
                    username TEXT NOT NULL COLLATE NOCASE,
                    client_key TEXT NOT NULL,
                    failed_count INTEGER NOT NULL,
                    window_started TEXT NOT NULL,
                    locked_until TEXT,
                    PRIMARY KEY (username, client_key)
                );
            """)
            self.conn.commit()

    def setup_required(self) -> bool:
        row = self.conn.execute("SELECT 1 FROM auth_users LIMIT 1").fetchone()
        return row is None

    @classmethod
    def validate_username(cls, username: str) -> str | None:
        if not cls._USERNAME_RE.fullmatch(username or ""):
            return "用户名需为 3-64 位字母、数字、点、横线或下划线"
        return None

    @staticmethod
    def validate_password(password: str) -> str | None:
        if len(password or "") < 12:
            return "密码长度不能少于 12 位"
        if len(password) > 256:
            return "密码长度不能超过 256 位"
        categories = sum((
            any(char.islower() for char in password),
            any(char.isupper() for char in password),
            any(char.isdigit() for char in password),
            any(not char.isalnum() for char in password),
        ))
        if categories < 3:
            return "密码需包含大小写字母、数字和符号中的至少三类"
        return None

    @classmethod
    def hash_password(cls, password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32
        )
        return f"scrypt$16384$8$1${cls._encode(salt)}${cls._encode(digest)}"

    @classmethod
    def _verify_hash(cls, password: str, encoded: str) -> bool:
        try:
            algorithm, n, r, p, salt, expected = encoded.split("$", 5)
            if algorithm != "scrypt":
                return False
            expected_bytes = cls._decode(expected)
            actual = hashlib.scrypt(
                password.encode("utf-8"), salt=cls._decode(salt),
                n=int(n), r=int(r), p=int(p), dklen=len(expected_bytes),
            )
            return secrets.compare_digest(actual, expected_bytes)
        except (ValueError, TypeError):
            return False

    def setup_admin(self, username: str, password: str) -> tuple[bool, str]:
        username_error = self.validate_username(username)
        if username_error:
            return False, username_error
        password_error = self.validate_password(password)
        if password_error:
            return False, password_error
        now = self._now().isoformat()
        with self._lock:
            if not self.setup_required():
                return False, "管理员已经初始化"
            try:
                self.conn.execute(
                    "INSERT INTO auth_users(username, password_hash, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (username, self.hash_password(password), now, now),
                )
                self.conn.commit()
            except sqlite3.IntegrityError:
                self.conn.rollback()
                return False, "管理员已经初始化"
        return True, ""

    def verify_password(self, username: str, password: str) -> bool:
        row = self.conn.execute(
            "SELECT password_hash FROM auth_users WHERE username=?", (username,)
        ).fetchone()
        encoded = row["password_hash"] if row else self._DUMMY_HASH
        verified = self._verify_hash(password, encoded)
        return bool(row) and verified

    def create_session(self, username: str) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        now = self._now()
        expires_at = now + timedelta(hours=self.SESSION_HOURS)
        with self._lock:
            self._delete_expired_sessions(now)
            self.conn.execute(
                "INSERT INTO auth_sessions(token_hash, username, csrf_hash, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (self._token_hash(token), username, self._token_hash(csrf_token),
                 now.isoformat(), expires_at.isoformat()),
            )
            self.conn.commit()
        return token, csrf_token

    def authenticate(self, token: str, source: str = "bearer") -> AuthIdentity | None:
        if not token:
            return None
        api_token = os.getenv("EMAIL_AGENT_API_TOKEN", "")
        if source == "bearer" and api_token and secrets.compare_digest(token, api_token):
            return AuthIdentity("mcpilot", "api_token")
        row = self.conn.execute(
            "SELECT username, csrf_hash, expires_at FROM auth_sessions WHERE token_hash=?",
            (self._token_hash(token),),
        ).fetchone()
        if not row:
            return None
        try:
            expires_at = datetime.fromisoformat(row["expires_at"])
        except (TypeError, ValueError):
            expires_at = self._now() - timedelta(seconds=1)
        if expires_at <= self._now():
            self.revoke_session(token)
            return None
        return AuthIdentity(row["username"], source, token, row["csrf_hash"])

    def verify_csrf(self, identity: AuthIdentity, csrf_token: str) -> bool:
        return bool(
            identity.source != "cookie"
            or csrf_token
            and secrets.compare_digest(identity.csrf_hash, self._token_hash(csrf_token))
        )

    def revoke_session(self, token: str):
        if not token:
            return
        with self._lock:
            self.conn.execute(
                "DELETE FROM auth_sessions WHERE token_hash=?", (self._token_hash(token),)
            )
            self.conn.commit()

    def revoke_user_sessions(self, username: str):
        with self._lock:
            self.conn.execute("DELETE FROM auth_sessions WHERE username=?", (username,))
            self.conn.commit()

    def _delete_expired_sessions(self, now: datetime | None = None):
        now = now or self._now()
        self.conn.execute("DELETE FROM auth_sessions WHERE expires_at<=?", (now.isoformat(),))

    def lock_seconds(self, username: str, client_key: str) -> int:
        row = self.conn.execute(
            "SELECT locked_until FROM auth_login_attempts WHERE username=? AND client_key=?",
            (username, client_key),
        ).fetchone()
        if not row or not row["locked_until"]:
            return 0
        try:
            remaining = (datetime.fromisoformat(row["locked_until"]) - self._now()).total_seconds()
        except (TypeError, ValueError):
            return 0
        return max(0, math.ceil(remaining))

    def record_login_failure(self, username: str, client_key: str) -> int:
        now = self._now()
        with self._lock:
            row = self.conn.execute(
                "SELECT failed_count, window_started, locked_until FROM auth_login_attempts "
                "WHERE username=? AND client_key=?",
                (username, client_key),
            ).fetchone()
            if row and row["locked_until"]:
                remaining = self.lock_seconds(username, client_key)
                if remaining:
                    return remaining
            fresh_window = True
            if row:
                try:
                    started = datetime.fromisoformat(row["window_started"])
                    fresh_window = now - started >= timedelta(minutes=self.FAILURE_WINDOW_MINUTES)
                except (TypeError, ValueError):
                    pass
            failed_count = 1 if not row or fresh_window else int(row["failed_count"]) + 1
            window_started = now if not row or fresh_window else datetime.fromisoformat(
                row["window_started"]
            )
            locked_until = None
            if failed_count >= self.MAX_FAILED_LOGINS:
                locked_until = now + timedelta(minutes=self.LOCK_MINUTES)
            self.conn.execute("""
                INSERT INTO auth_login_attempts
                    (username, client_key, failed_count, window_started, locked_until)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(username, client_key) DO UPDATE SET
                    failed_count=excluded.failed_count,
                    window_started=excluded.window_started,
                    locked_until=excluded.locked_until
            """, (
                username, client_key, failed_count, window_started.isoformat(),
                locked_until.isoformat() if locked_until else None,
            ))
            self.conn.commit()
        return self.LOCK_MINUTES * 60 if locked_until else 0

    def clear_login_failures(self, username: str, client_key: str):
        with self._lock:
            self.conn.execute(
                "DELETE FROM auth_login_attempts WHERE username=? AND client_key=?",
                (username, client_key),
            )
            self.conn.commit()

    def change_password(self, username: str, current_password: str,
                        new_password: str) -> tuple[bool, str]:
        if not self.verify_password(username, current_password):
            return False, "当前密码错误"
        password_error = self.validate_password(new_password)
        if password_error:
            return False, password_error
        now = self._now().isoformat()
        with self._lock:
            self.conn.execute(
                "UPDATE auth_users SET password_hash=?, updated_at=? WHERE username=?",
                (self.hash_password(new_password), now, username),
            )
            self.conn.execute("DELETE FROM auth_sessions WHERE username=?", (username,))
            self.conn.commit()
        return True, ""


def get_auth_manager() -> AuthManager:
    return current_app.extensions["auth"]


def current_identity() -> AuthIdentity | None:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return get_auth_manager().authenticate(auth_header[7:], "bearer")
    token = request.cookies.get(AuthManager.COOKIE_NAME, "")
    return get_auth_manager().authenticate(token, "cookie")


def token_required(function):
    """Require a persisted browser session or configured API bearer token."""

    @functools.wraps(function)
    def decorated(*args, **kwargs):
        identity = current_identity()
        if not identity:
            return jsonify({"error": "认证令牌无效或已过期"}), 401
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            csrf_token = request.headers.get("X-CSRF-Token", "")
            if not get_auth_manager().verify_csrf(identity, csrf_token):
                return jsonify({"error": "安全校验失败，请刷新页面后重试"}), 403
        request.current_user = identity.username
        request.current_auth = identity
        return function(*args, **kwargs)

    return decorated


def get_current_user() -> str | None:
    return getattr(request, "current_user", None)


def set_auth_cookies(response, token: str, csrf_token: str):
    secure = request.is_secure or os.getenv("EMAIL_AGENT_SECURE_COOKIES", "").lower() in {
        "1", "true", "yes", "on",
    }
    max_age = AuthManager.SESSION_HOURS * 60 * 60
    response.set_cookie(
        AuthManager.COOKIE_NAME, token, max_age=max_age, httponly=True,
        secure=secure, samesite="Strict", path="/",
    )
    response.set_cookie(
        AuthManager.CSRF_COOKIE_NAME, csrf_token, max_age=max_age, httponly=False,
        secure=secure, samesite="Strict", path="/",
    )
    return response


def clear_auth_cookies(response):
    response.delete_cookie(AuthManager.COOKIE_NAME, path="/", samesite="Strict")
    response.delete_cookie(AuthManager.CSRF_COOKIE_NAME, path="/", samesite="Strict")
    return response
