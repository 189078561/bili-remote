from __future__ import annotations

from bili_remote.security import SecretBox, SessionService, redact_sensitive


def test_secret_box_round_trip(settings) -> None:
    box = SecretBox(settings.app_secret)
    encrypted = box.encrypt_json('{"SESSDATA":"secret"}')
    assert "secret" not in encrypted
    assert box.decrypt_json(encrypted) == '{"SESSDATA":"secret"}'


def test_sessions_are_signed(settings) -> None:
    service = SessionService(settings)
    token, session = service.issue("admin")
    parsed = service.parse(token)
    assert parsed is not None
    assert parsed.username == "admin"
    assert parsed.csrf == session.csrf
    assert service.parse(token + "tampered") is None


def test_sensitive_values_are_redacted() -> None:
    text = (
        "Cookie: SESSDATA=abc123; bili_jct=def456 "
        "qrcode_key=ghi789 access_key=jkl012"
    )
    cleaned = redact_sensitive(text)
    for secret in ("abc123", "def456", "ghi789", "jkl012"):
        assert secret not in cleaned
    assert "***" in cleaned
