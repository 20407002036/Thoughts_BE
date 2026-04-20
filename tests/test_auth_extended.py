import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.auth_service import (
    AuthConflictError,
    AuthUnauthorizedError,
    AuthUpstreamError,
    AuthValidationError,
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_sign_up_success_with_session(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_sign_up(self, email: str, password: str) -> dict[str, object]:
        assert email == "new@example.com"
        assert password == "secret123"
        return {
            "user_id": "user-123",
            "email": "new@example.com",
            "email_confirmed": True,
            "access_token": "access-token-123",
            "token_type": "bearer",
            "expires_in": 3600,
            "refresh_token": "refresh-token-123",
        }

    monkeypatch.setattr("app.services.auth_service.AuthService.sign_up", _fake_sign_up)

    response = client.post(
        "/v1/auth/signup",
        json={"email": "new@example.com", "password": "secret123"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["user_id"] == "user-123"
    assert payload["email"] == "new@example.com"
    assert payload["email_confirmed"] is True
    assert payload["access_token"] == "access-token-123"


def test_sign_up_success_requires_email_confirmation(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_sign_up(self, email: str, password: str) -> dict[str, object]:
        return {
            "user_id": "user-456",
            "email": "pending@example.com",
            "email_confirmed": False,
            "access_token": None,
            "token_type": None,
            "expires_in": None,
            "refresh_token": None,
        }

    monkeypatch.setattr("app.services.auth_service.AuthService.sign_up", _fake_sign_up)

    response = client.post(
        "/v1/auth/signup",
        json={"email": "pending@example.com", "password": "secret123"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["email_confirmed"] is False
    assert payload["access_token"] is None


def test_sign_up_returns_400_for_validation_error(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_sign_up(self, email: str, password: str) -> dict[str, object]:
        raise AuthValidationError("Password should be at least 6 characters")

    monkeypatch.setattr("app.services.auth_service.AuthService.sign_up", _fake_sign_up)

    response = client.post(
        "/v1/auth/signup",
        json={"email": "new@example.com", "password": "secret123"},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"] == "bad_request"
    assert payload["message"] == "Password should be at least 6 characters"


def test_sign_up_returns_409_for_existing_user(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_sign_up(self, email: str, password: str) -> dict[str, object]:
        raise AuthConflictError("User already exists")

    monkeypatch.setattr("app.services.auth_service.AuthService.sign_up", _fake_sign_up)

    response = client.post(
        "/v1/auth/signup",
        json={"email": "existing@example.com", "password": "secret123"},
    )

    assert response.status_code == 409
    payload = response.json()
    assert payload["error"] == "conflict"
    assert payload["message"] == "User already exists"


def test_refresh_success(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_refresh(self, refresh_token: str) -> dict[str, object]:
        assert refresh_token == "refresh-token-123"
        return {
            "access_token": "new-access-token",
            "token_type": "bearer",
            "expires_in": 3600,
            "refresh_token": "new-refresh-token",
            "user": {"id": "user-123", "email": "user@example.com"},
        }

    monkeypatch.setattr("app.services.auth_service.AuthService.refresh_session", _fake_refresh)

    response = client.post(
        "/v1/auth/refresh",
        json={"refresh_token": "refresh-token-123"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["access_token"] == "new-access-token"
    assert payload["refresh_token"] == "new-refresh-token"
    assert payload["user_id"] == "user-123"


def test_refresh_returns_401_for_invalid_token(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_refresh(self, refresh_token: str) -> dict[str, object]:
        raise AuthUnauthorizedError("Invalid refresh token")

    monkeypatch.setattr("app.services.auth_service.AuthService.refresh_session", _fake_refresh)

    response = client.post(
        "/v1/auth/refresh",
        json={"refresh_token": "bad-refresh-token"},
    )

    assert response.status_code == 401
    payload = response.json()
    assert payload["error"] == "unauthorized"
    assert payload["message"] == "Invalid refresh token"


def test_logout_success(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_sign_out(self, access_token: str) -> None:
        assert access_token == "access-token-123"

    monkeypatch.setattr("app.services.auth_service.AuthService.sign_out", _fake_sign_out)

    response = client.post(
        "/v1/auth/logout",
        headers={"Authorization": "Bearer access-token-123"},
    )

    assert response.status_code == 200
    assert response.json() == {"success": True}


def test_logout_requires_token(client: TestClient) -> None:
    response = client.post("/v1/auth/logout")

    assert response.status_code == 401
    payload = response.json()
    assert payload["error"] == "unauthorized"
    assert payload["message"] == "Missing bearer token"


def test_logout_returns_401_for_invalid_access_token(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_sign_out(self, access_token: str) -> None:
        raise AuthUnauthorizedError("Invalid access token")

    monkeypatch.setattr("app.services.auth_service.AuthService.sign_out", _fake_sign_out)

    response = client.post(
        "/v1/auth/logout",
        headers={"Authorization": "Bearer bad-token"},
    )

    assert response.status_code == 401
    payload = response.json()
    assert payload["error"] == "unauthorized"
    assert payload["message"] == "Invalid access token"


def test_logout_returns_502_for_upstream_failure(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_sign_out(self, access_token: str) -> None:
        raise AuthUpstreamError("Unable to reach Supabase auth service")

    monkeypatch.setattr("app.services.auth_service.AuthService.sign_out", _fake_sign_out)

    response = client.post(
        "/v1/auth/logout",
        headers={"Authorization": "Bearer access-token-123"},
    )

    assert response.status_code == 502
    payload = response.json()
    assert payload["error"] == "upstream_error"
    assert payload["message"] == "Unable to reach Supabase auth service"
