import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.core.security import AuthenticatedUser, get_current_user
from app.core.settings import Settings


def test_get_current_user_returns_dev_user_when_auth_disabled() -> None:
    settings = Settings(auth_required=False, supabase_url=None, supabase_service_role_key=None)

    user = get_current_user(credentials=None, settings=settings)

    assert user == AuthenticatedUser(user_id="dev-local-user", email="dev@example.com")


def test_get_current_user_rejects_auth_disabled_with_supabase_enabled() -> None:
    settings = Settings(
        auth_required=False,
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="a.b.c",
    )

    with pytest.raises(HTTPException) as exc_info:
        get_current_user(credentials=None, settings=settings)

    assert exc_info.value.status_code == 400
    assert "AUTH_REQUIRED=false is incompatible with Supabase persistence" in str(exc_info.value.detail)


def test_get_current_user_requires_bearer_token_when_auth_enabled() -> None:
    settings = Settings(auth_required=True)

    with pytest.raises(HTTPException) as exc_info:
        get_current_user(credentials=None, settings=settings)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Missing bearer token"


def test_get_current_user_rejects_invalid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(auth_required=True, supabase_url="https://example.supabase.co")
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="bad-token")

    def _raise_invalid(*args: object, **kwargs: object) -> dict[str, str]:
        raise jwt.InvalidTokenError("invalid token")

    monkeypatch.setattr("app.core.security._decode_token", _raise_invalid)

    with pytest.raises(HTTPException) as exc_info:
        get_current_user(credentials=credentials, settings=settings)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid token"


def test_get_current_user_extracts_user_from_valid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(auth_required=True, supabase_url="https://example.supabase.co")
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="valid-token")

    monkeypatch.setattr("app.core.security._decode_token", lambda _token, _settings: {"sub": "user-123", "email": "user@example.com"})

    user = get_current_user(credentials=credentials, settings=settings)

    assert user.user_id == "user-123"
    assert user.email == "user@example.com"


def test_get_current_user_accepts_hs256_token_with_jwt_secret() -> None:
    settings = Settings(
        auth_required=True,
        supabase_url="https://example.supabase.co",
        supabase_jwt_secret="super-secret",
        supabase_jwt_audience="authenticated",
    )
    token = jwt.encode(
        {
            "sub": "user-123",
            "email": "user@example.com",
            "aud": "authenticated",
            "iss": "https://example.supabase.co/auth/v1",
        },
        "super-secret",
        algorithm="HS256",
    )
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    user = get_current_user(credentials=credentials, settings=settings)

    assert user.user_id == "user-123"
    assert user.email == "user@example.com"
