import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.auth_service import (
    AuthInvalidCredentialsError,
    AuthServiceConfigError,
    AuthUpstreamError,
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_sign_in_success(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_sign_in(self, email: str, password: str) -> dict[str, object]:
        assert email == "user@example.com"
        assert password == "secret123"
        return {
            "access_token": "access-token-123",
            "token_type": "bearer",
            "expires_in": 3600,
            "refresh_token": "refresh-token-123",
            "user": {"id": "user-123", "email": "user@example.com"},
        }

    monkeypatch.setattr("app.services.auth_service.AuthService.sign_in", _fake_sign_in)

    response = client.post(
        "/v1/auth/login",
        json={"email": "user@example.com", "password": "secret123"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["access_token"] == "access-token-123"
    assert payload["token_type"] == "bearer"
    assert payload["user_id"] == "user-123"
    assert payload["email"] == "user@example.com"


def test_sign_in_rejects_invalid_credentials(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_sign_in(self, email: str, password: str) -> dict[str, object]:
        raise AuthInvalidCredentialsError("Invalid email or password")

    monkeypatch.setattr("app.services.auth_service.AuthService.sign_in", _fake_sign_in)

    response = client.post(
        "/v1/auth/login",
        json={"email": "user@example.com", "password": "wrong-password"},
    )

    assert response.status_code == 401
    payload = response.json()
    assert payload["error"] == "unauthorized"
    assert payload["message"] == "Invalid email or password"


def test_sign_in_returns_500_for_missing_config(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_sign_in(self, email: str, password: str) -> dict[str, object]:
        raise AuthServiceConfigError("Supabase sign-in requires SUPABASE_URL and SUPABASE_ANON_KEY")

    monkeypatch.setattr("app.services.auth_service.AuthService.sign_in", _fake_sign_in)

    response = client.post(
        "/v1/auth/login",
        json={"email": "user@example.com", "password": "secret123"},
    )

    assert response.status_code == 500
    payload = response.json()
    assert payload["error"] == "internal_error"
    assert payload["message"] == "Supabase sign-in requires SUPABASE_URL and SUPABASE_ANON_KEY"


def test_sign_in_returns_502_for_upstream_failure(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_sign_in(self, email: str, password: str) -> dict[str, object]:
        raise AuthUpstreamError("Unable to reach Supabase auth service")

    monkeypatch.setattr("app.services.auth_service.AuthService.sign_in", _fake_sign_in)

    response = client.post(
        "/v1/auth/login",
        json={"email": "user@example.com", "password": "secret123"},
    )

    assert response.status_code == 502
    payload = response.json()
    assert payload["error"] == "upstream_error"
    assert payload["message"] == "Unable to reach Supabase auth service"
