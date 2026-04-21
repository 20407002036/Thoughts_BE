from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.profile import get_profile_service
from app.core.security import AuthenticatedUser, get_current_user
from app.main import app
from app.repositories.profile_repository import ProfileRepositoryError


class _StubProfileService:
    def __init__(self) -> None:
        self._profile = {
            "user_id": "user-1",
            "email": "user@example.com",
            "display_name": "Calm Mind",
            "streak_count": 2,
            "last_journal_saved": datetime(2026, 4, 21, tzinfo=timezone.utc),
            "created_at": datetime(2026, 4, 20, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 4, 21, tzinfo=timezone.utc),
        }

    def get_profile(self, user_id: str, email: str | None) -> dict[str, object]:
        assert user_id == "user-1"
        assert email == "user@example.com"
        return self._profile

    def update_display_name(self, user_id: str, email: str | None, display_name: str) -> dict[str, object]:
        assert user_id == "user-1"
        assert email == "user@example.com"
        self._profile["display_name"] = display_name.strip()
        return self._profile


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_get_profile_success(client: TestClient) -> None:
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="user-1", email="user@example.com")
    app.dependency_overrides[get_profile_service] = lambda: _StubProfileService()

    try:
        response = client.get("/v1/profile")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["user_id"] == "user-1"
    assert payload["email"] == "user@example.com"
    assert payload["display_name"] == "Calm Mind"
    assert payload["streak_count"] == 2


def test_patch_profile_success(client: TestClient) -> None:
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="user-1", email="user@example.com")
    app.dependency_overrides[get_profile_service] = lambda: _StubProfileService()

    try:
        response = client.patch("/v1/profile", json={"display_name": "  New Name  "})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["display_name"] == "New Name"


def test_patch_profile_rejects_empty_display_name(client: TestClient) -> None:
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="user-1", email="user@example.com")
    app.dependency_overrides[get_profile_service] = lambda: _StubProfileService()

    try:
        response = client.patch("/v1/profile", json={"display_name": ""})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    payload = response.json()
    assert payload["error"] == "validation_error"


def test_get_profile_maps_repository_error_to_502(client: TestClient) -> None:
    class _FailingService:
        def get_profile(self, user_id: str, email: str | None) -> dict[str, object]:
            raise ProfileRepositoryError("Supabase profile select failed: relation missing")

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="user-1", email="user@example.com")
    app.dependency_overrides[get_profile_service] = lambda: _FailingService()

    try:
        response = client.get("/v1/profile")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    payload = response.json()
    assert payload["error"] == "upstream_error"
    assert payload["message"] == "Supabase profile select failed: relation missing"
