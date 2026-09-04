"""密码哈希（Argon2id）、会话令牌（HMAC-SHA256 哈希后入库）。"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from .config import settings

SESSION_COOKIE = "rag_session"

_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, plain)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def new_session_token() -> str:
    """高熵随机会话令牌（只以哈希形式入库、只以明文形式放 Cookie）。"""
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """以 RAG_SECRET_KEY 为密钥的 HMAC-SHA256；密钥缺失时回退固定开发密钥（并另行告警）。"""
    key = (settings.secret_key or "rag-pilot-dev-key-change-me").encode("utf-8")
    return hmac.new(key, token.encode("utf-8"), hashlib.sha256).hexdigest()
