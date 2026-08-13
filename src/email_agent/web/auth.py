"""认证中间件和工具。"""
import functools
import hashlib
import os
import secrets
from datetime import datetime, timedelta

from flask import jsonify, request


_active_tokens = {}


class AuthManager:
    """认证管理器。"""

    USERS = {
        "admin": {
            "password_hash": "240be518fabd2724ddb6f04eeb1da5967448d7e831c08c8fa822809f74c720a9",
            "username": "admin",
        }
    }
    TOKEN_EXPIRE_HOURS = 24

    @staticmethod
    def hash_password(password: str) -> str:
        return hashlib.sha256(password.encode()).hexdigest()

    @classmethod
    def verify_password(cls, username: str, password: str) -> bool:
        user = cls.USERS.get(username)
        return bool(user and cls.hash_password(password) == user["password_hash"])

    @classmethod
    def generate_token(cls, username: str) -> str:
        token = secrets.token_urlsafe(32)
        expire_at = datetime.now() + timedelta(hours=cls.TOKEN_EXPIRE_HOURS)
        _active_tokens[token] = {"username": username, "expire_at": expire_at}
        cls._cleanup_expired_tokens()
        return token

    @classmethod
    def verify_token(cls, token: str) -> str | None:
        if not token:
            return None

        api_token = os.getenv("EMAIL_AGENT_API_TOKEN", "")
        if api_token and secrets.compare_digest(token, api_token):
            return "mcpilot"

        token_data = _active_tokens.get(token)
        if not token_data:
            return None
        if datetime.now() > token_data["expire_at"]:
            _active_tokens.pop(token, None)
            return None
        return token_data["username"]

    @classmethod
    def revoke_token(cls, token: str):
        _active_tokens.pop(token, None)

    @classmethod
    def _cleanup_expired_tokens(cls):
        now = datetime.now()
        expired = [
            token for token, data in _active_tokens.items()
            if now > data["expire_at"]
        ]
        for token in expired:
            _active_tokens.pop(token, None)


def token_required(f):
    """要求请求携带有效的 Bearer Token。"""

    @functools.wraps(f)
    def decorated(*args, **kwargs):
        token = None
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]
        if not token:
            return jsonify({"error": "未提供认证令牌"}), 401

        username = AuthManager.verify_token(token)
        if not username:
            return jsonify({"error": "认证令牌无效或已过期"}), 401
        request.current_user = username
        return f(*args, **kwargs)

    return decorated


def get_current_user() -> str | None:
    """获取当前认证用户。"""
    return getattr(request, "current_user", None)
