"""Device registration API: /v1/devices.

Exercises the real DeviceRepository against its in-memory fallback (no Supabase
client configured), which is the same code path local development uses, plus the
PushService preference gate via a stub.
"""
import pytest
from fastapi.testclient import TestClient

from app.api.devices import get_device_repository, get_push_service
from app.core.security import AuthenticatedUser, get_current_user
from app.core.settings import Settings
from app.main import app
from app.repositories.device_repository import DeviceRepository
from app.services.push_service import PushService

USER_ID = "user-1"
OTHER_USER_ID = "user-2"
TOKEN = "fcm-token-abcdefghijklmnop"
OTHER_TOKEN = "fcm-token-zyxwvutsrqponmlk"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def settings() -> Settings:
    """Local-fallback settings: no Supabase client, no Firebase credentials."""
    return Settings(supabase_url=None, supabase_service_role_key=None, firebase_enabled=False)


@pytest.fixture
def repository(settings: Settings) -> DeviceRepository:
    return DeviceRepository(settings)


@pytest.fixture
def auth(repository: DeviceRepository, settings: Settings):
    from app.repositories.profile_repository import ProfileRepository

    push_service = PushService(
        settings=settings,
        device_repository=repository,
        profile_repository=ProfileRepository(settings),
    )
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id=USER_ID, email="user@example.com")
    app.dependency_overrides[get_device_repository] = lambda: repository
    app.dependency_overrides[get_push_service] = lambda: push_service
    yield
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_device_repository, None)
    app.dependency_overrides.pop(get_push_service, None)


def test_register_device_creates_row(client: TestClient, auth, repository) -> None:
    response = client.post("/v1/devices", json={"token": TOKEN, "platform": "android", "app_version": "1.4.0"})

    assert response.status_code == 201
    body = response.json()
    assert body["registered"] is True
    assert body["platform"] == "android"
    assert body["device_id"]
    assert repository.list_active_tokens(USER_ID) == [TOKEN]


def test_register_device_is_idempotent(client: TestClient, auth, repository) -> None:
    """The mobile client re-registers on every launch and on token rotation."""
    first = client.post("/v1/devices", json={"token": TOKEN, "platform": "android"})
    second = client.post("/v1/devices", json={"token": TOKEN, "platform": "ios", "app_version": "2.0.0"})

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["device_id"] == first.json()["device_id"]
    assert second.json()["platform"] == "ios"
    assert repository.list_active_tokens(USER_ID) == [TOKEN]


def test_register_device_reassigns_token_from_another_user(client: TestClient, auth, repository) -> None:
    """A shared device that changes accounts must stop pushing to the old one."""
    repository.register_device(user_id=OTHER_USER_ID, token=TOKEN)

    response = client.post("/v1/devices", json={"token": TOKEN, "platform": "ios"})

    assert response.status_code == 201
    assert repository.list_active_tokens(OTHER_USER_ID) == []
    assert repository.list_active_tokens(USER_ID) == [TOKEN]


def test_register_device_rejects_unsupported_platform(client: TestClient, auth) -> None:
    response = client.post("/v1/devices", json={"token": TOKEN, "platform": "windows"})

    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"


def test_register_device_rejects_short_token(client: TestClient, auth) -> None:
    response = client.post("/v1/devices", json={"token": "short"})

    assert response.status_code == 422


def test_unregister_device_removes_token(client: TestClient, auth, repository) -> None:
    client.post("/v1/devices", json={"token": TOKEN})

    response = client.delete(f"/v1/devices/{TOKEN}")

    assert response.status_code == 200
    assert response.json() == {"success": True, "removed": True}
    assert repository.list_active_tokens(USER_ID) == []


def test_unregister_unknown_token_is_idempotent(client: TestClient, auth) -> None:
    """Sign-out runs on clients that may never have registered; that must not 404."""
    response = client.delete(f"/v1/devices/{OTHER_TOKEN}")

    assert response.status_code == 200
    assert response.json() == {"success": True, "removed": False}


def test_unregister_cannot_touch_another_users_device(client: TestClient, auth, repository) -> None:
    repository.register_device(user_id=OTHER_USER_ID, token=OTHER_TOKEN)

    response = client.delete(f"/v1/devices/{OTHER_TOKEN}")

    assert response.status_code == 200
    assert response.json()["removed"] is False
    assert repository.list_active_tokens(OTHER_USER_ID) == [OTHER_TOKEN]


def test_register_device_requires_authentication(client: TestClient, repository) -> None:
    response = client.post("/v1/devices", json={"token": TOKEN})

    assert response.status_code == 401


def test_multiple_devices_accumulate(client: TestClient, auth, repository) -> None:
    client.post("/v1/devices", json={"token": TOKEN, "platform": "android"})
    client.post("/v1/devices", json={"token": OTHER_TOKEN, "platform": "web"})

    assert sorted(repository.list_active_tokens(USER_ID)) == sorted([TOKEN, OTHER_TOKEN])


def test_test_push_endpoint_reports_status(client: TestClient, auth, repository) -> None:
    """With no Firebase config the send is a deliberate no-op, not an error."""
    client.post("/v1/devices", json={"token": TOKEN})

    response = client.post("/v1/devices/test")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"sent", "skipped"}
    if body["status"] == "skipped":
        assert body["reason"] == "firebase_not_configured"
