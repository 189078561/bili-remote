from __future__ import annotations

import base64
import hashlib
import re
import secrets
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import Settings

_SENSITIVE_PATTERN = re.compile(
    r"(?i)(SESSDATA|bili_jct|DedeUserID__ckMd5|access_key|refresh_token|"
    r"qrcode_key)([=:]\s*)[^&\s\"']+"
)


def redact_sensitive(text: str) -> str:
    return _SENSITIVE_PATTERN.sub(r"\1\2***", text)


class SecretBox:
    def __init__(self, app_secret: str) -> None:
        digest = hashlib.sha256(app_secret.encode("utf-8")).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt_json(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt_json(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError) as exc:
            raise ValueError("Stored credential cannot be decrypted") from exc


class PasswordService:
    def __init__(self, settings: Settings) -> None:
        self._hasher = PasswordHasher()
        self._stored_hash = settings.admin_password_hash or self._hasher.hash(
            settings.admin_password
        )

    def verify(self, password: str) -> bool:
        try:
            return self._hasher.verify(self._stored_hash, password)
        except (VerifyMismatchError, InvalidHashError):
            return False


@dataclass(slots=True)
class SessionData:
    username: str
    csrf: str


class SessionService:
    cookie_name = "bili_remote_session"

    def __init__(self, settings: Settings) -> None:
        self._serializer = URLSafeTimedSerializer(
            settings.app_secret, salt="bili-remote-session"
        )
        self._max_age = settings.session_max_age_seconds

    def issue(self, username: str) -> tuple[str, SessionData]:
        session = SessionData(username=username, csrf=secrets.token_urlsafe(32))
        token = self._serializer.dumps(
            {"username": session.username, "csrf": session.csrf}
        )
        return token, session

    def parse(self, token: str | None) -> SessionData | None:
        if not token:
            return None
        try:
            payload = self._serializer.loads(token, max_age=self._max_age)
        except (BadSignature, SignatureExpired):
            return None
        if not isinstance(payload, dict):
            return None
        username = payload.get("username")
        csrf = payload.get("csrf")
        if not isinstance(username, str) or not isinstance(csrf, str):
            return None
        return SessionData(username=username, csrf=csrf)
