"""PushService: preference gating, batching, dead-token pruning, failure isolation.

The firebase_admin SDK is stubbed at the module boundary (`_get_messaging_module`)
so these tests never touch the network or need a service account.
"""
from typing import Any

import pytest
from firebase_admin import exceptions, messaging

from app.core.settings import Settings
from app.repositories.device_repository import DeviceRepository
from app.repositories.profile_repository import ProfileRepository
from app.services import push_service as push_module
from app.services.push_service import (
    SEND_ERROR_DEAD_TOKEN,
    SEND_ERROR_RETRY,
    SEND_ERROR_UNKNOWN,
    PushService,
    classify_send_error,
)

USER_ID = "user-1"
ENTRY_ID = "entry-1"
LIVE_TOKEN = "fcm-token-live-0001"
DEAD_TOKEN = "fcm-token-dead-0001"


def _settings(**overrides: Any) -> Settings:
    base = {
        "supabase_url": None,
        "supabase_service_role_key": None,
        "firebase_enabled": True,
        "firebase_project_id": "test-project",
    }
    base.update(overrides)
    return Settings(**base)


class _FakeResponse:
    def __init__(self, success: bool, exception: Exception | None = None) -> None:
        self.success = success
        self.exception = exception


class _FakeBatchResponse:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.responses = responses


class _FakeMessaging:
    """Stands in for firebase_admin.messaging."""

    def __init__(self, responder=None, batch_error: Exception | None = None) -> None:
        self._responder = responder or (lambda _token: _FakeResponse(True))
        self._batch_error = batch_error
        self.calls: list[dict[str, Any]] = []

    class _Notification:
        def __init__(self, title: str, body: str) -> None:
            self.title = title
            self.body = body

    class _AndroidConfig:
        def __init__(self, priority: str) -> None:
            self.priority = priority

    class _APNSConfig:
        def __init__(self, headers: dict[str, str]) -> None:
            self.headers = headers

    class _MulticastMessage:
        def __init__(self, tokens, notification, data, android, apns) -> None:
            self.tokens = tokens
            self.notification = notification
            self.data = data
            self.android = android
            self.apns = apns

    Notification = _Notification
    AndroidConfig = _AndroidConfig
    APNSConfig = _APNSConfig
    MulticastMessage = _MulticastMessage

    # The classifier inspects these real SDK exception types, so the stand-in
    # module has to expose the same names the real one does.
    UnregisteredError = messaging.UnregisteredError
    SenderIdMismatchError = messaging.SenderIdMismatchError
    QuotaExceededError = messaging.QuotaExceededError
    ThirdPartyAuthError = messaging.ThirdPartyAuthError

    def send_each_for_multicast(self, message, dry_run: bool = False) -> _FakeBatchResponse:
        self.calls.append(
            {
                "tokens": list(message.tokens),
                "title": message.notification.title,
                "body": message.notification.body,
                "data": dict(message.data),
                "android_priority": message.android.priority,
                "apns_headers": dict(message.apns.headers),
                "dry_run": dry_run,
            }
        )
        if self._batch_error is not None:
            raise self._batch_error
        return _FakeBatchResponse([self._responder(token) for token in message.tokens])

@pytest.fixture
def fake_messaging(monkeypatch: pytest.MonkeyPatch) -> _FakeMessaging:
    messaging = _FakeMessaging()
    monkeypatch.setattr(push_module, "_get_messaging_module", lambda: messaging)
    monkeypatch.setattr(push_module, "initialize_firebase_app", lambda _settings: object())
    return messaging


def _service(settings: Settings, devices: DeviceRepository, profiles: ProfileRepository) -> PushService:
    return PushService(settings=settings, device_repository=devices, profile_repository=profiles)


def _notifications_enabled(profiles: ProfileRepository, enabled: bool) -> None:
    profile = profiles.get_profile(USER_ID)
    profile["notifications_enabled"] = enabled
    profiles._local_profiles[USER_ID] = profile


def test_notify_journal_ready_sends_to_registered_device(fake_messaging: _FakeMessaging) -> None:
    settings = _settings()
    devices = DeviceRepository(settings)
    profiles = ProfileRepository(settings)
    devices.register_device(USER_ID, LIVE_TOKEN, platform="android")

    result = _service(settings, devices, profiles).notify_journal_ready(user_id=USER_ID, entry_id=ENTRY_ID)

    assert result.status == "sent"
    assert result.sent == 1
    assert result.failed == 0
    assert len(fake_messaging.calls) == 1
    call = fake_messaging.calls[0]
    assert call["tokens"] == [LIVE_TOKEN]
    assert call["data"] == {"type": "journal_ready", "entry_id": ENTRY_ID}


def test_notification_body_never_carries_journal_content(fake_messaging: _FakeMessaging) -> None:
    """Journal text is encrypted at rest and must not leave the database."""
    settings = _settings()
    devices = DeviceRepository(settings)
    profiles = ProfileRepository(settings)
    devices.register_device(USER_ID, LIVE_TOKEN)

    _service(settings, devices, profiles).notify_journal_ready(user_id=USER_ID, entry_id=ENTRY_ID)

    call = fake_messaging.calls[0]
    assert ENTRY_ID in call["data"].values()
    assert ENTRY_ID not in call["title"]
    assert ENTRY_ID not in call["body"]


def test_send_is_skipped_when_notifications_disabled(fake_messaging: _FakeMessaging) -> None:
    settings = _settings()
    devices = DeviceRepository(settings)
    profiles = ProfileRepository(settings)
    devices.register_device(USER_ID, LIVE_TOKEN)
    _notifications_enabled(profiles, False)

    result = _service(settings, devices, profiles).notify_journal_ready(user_id=USER_ID, entry_id=ENTRY_ID)

    assert result.status == "skipped"
    assert result.reason == "notifications_disabled"
    assert fake_messaging.calls == []


def test_send_is_skipped_when_firebase_not_configured(fake_messaging: _FakeMessaging) -> None:
    settings = _settings(firebase_enabled=False)
    devices = DeviceRepository(settings)
    profiles = ProfileRepository(settings)
    devices.register_device(USER_ID, LIVE_TOKEN)

    result = _service(settings, devices, profiles).notify_journal_ready(user_id=USER_ID, entry_id=ENTRY_ID)

    assert result.status == "skipped"
    assert result.reason == "firebase_not_configured"
    assert fake_messaging.calls == []


def test_send_is_skipped_when_user_has_no_devices(fake_messaging: _FakeMessaging) -> None:
    settings = _settings()
    devices = DeviceRepository(settings)
    profiles = ProfileRepository(settings)

    result = _service(settings, devices, profiles).notify_journal_ready(user_id=USER_ID, entry_id=ENTRY_ID)

    assert result.status == "skipped"
    assert result.reason == "no_active_devices"
    assert fake_messaging.calls == []


def test_dead_tokens_are_pruned_and_not_retried(fake_messaging: _FakeMessaging) -> None:
    """An uninstalled app is the one failure that retires a registration."""

    def responder(token: str) -> _FakeResponse:
        if token == DEAD_TOKEN:
            return _FakeResponse(False, exception=messaging.UnregisteredError("token not registered"))
        return _FakeResponse(True)

    fake_messaging._responder = responder
    settings = _settings()
    devices = DeviceRepository(settings)
    profiles = ProfileRepository(settings)
    devices.register_device(USER_ID, LIVE_TOKEN)
    devices.register_device(USER_ID, DEAD_TOKEN)

    result = _service(settings, devices, profiles).notify_journal_ready(user_id=USER_ID, entry_id=ENTRY_ID)

    assert result.sent == 1
    assert result.failed == 1
    assert result.deactivated == 1
    assert devices.list_active_tokens(USER_ID) == [LIVE_TOKEN]


def test_transport_errors_keep_tokens_active(fake_messaging: _FakeMessaging) -> None:
    """A quota or auth failure is not the device's fault — keep the token."""
    fake_messaging._responder = lambda _token: _FakeResponse(False, exception=messaging.QuotaExceededError("slow down"))
    settings = _settings()
    devices = DeviceRepository(settings)
    profiles = ProfileRepository(settings)
    devices.register_device(USER_ID, LIVE_TOKEN)

    result = _service(settings, devices, profiles).notify_journal_ready(user_id=USER_ID, entry_id=ENTRY_ID)

    assert result.sent == 0
    assert result.failed == 1
    assert result.deactivated == 0
    assert devices.list_active_tokens(USER_ID) == [LIVE_TOKEN]


def test_apns_auth_failure_keeps_tokens_active(fake_messaging: _FakeMessaging) -> None:
    """A broken APNs certificate is our problem, not the device's."""
    fake_messaging._responder = lambda _token: _FakeResponse(
        False, exception=messaging.ThirdPartyAuthError("invalid apns certificate")
    )
    settings = _settings()
    devices = DeviceRepository(settings)
    profiles = ProfileRepository(settings)
    devices.register_device(USER_ID, LIVE_TOKEN)

    result = _service(settings, devices, profiles).notify_journal_ready(user_id=USER_ID, entry_id=ENTRY_ID)

    assert result.deactivated == 0
    assert devices.list_active_tokens(USER_ID) == [LIVE_TOKEN]


def test_sender_id_mismatch_prunes_the_token(fake_messaging: _FakeMessaging) -> None:
    """A token minted by another Firebase project can never work for us."""
    fake_messaging._responder = lambda _token: _FakeResponse(
        False, exception=messaging.SenderIdMismatchError("wrong project")
    )
    settings = _settings()
    devices = DeviceRepository(settings)
    profiles = ProfileRepository(settings)
    devices.register_device(USER_ID, LIVE_TOKEN)

    result = _service(settings, devices, profiles).notify_journal_ready(user_id=USER_ID, entry_id=ENTRY_ID)

    assert result.deactivated == 1
    assert devices.list_active_tokens(USER_ID) == []


def test_batch_failure_is_absorbed(fake_messaging: _FakeMessaging) -> None:
    """A whole batch failing must not raise into the ingest that triggered it."""
    fake_messaging._batch_error = RuntimeError("firebase unavailable")
    settings = _settings()
    devices = DeviceRepository(settings)
    profiles = ProfileRepository(settings)
    devices.register_device(USER_ID, LIVE_TOKEN)

    result = _service(settings, devices, profiles).notify_journal_ready(user_id=USER_ID, entry_id=ENTRY_ID)

    assert result.status == "sent"
    assert result.sent == 0
    assert result.failed == 1
    assert devices.list_active_tokens(USER_ID) == [LIVE_TOKEN]


def test_token_lookup_failure_is_absorbed(fake_messaging: _FakeMessaging) -> None:
    settings = _settings()
    devices = DeviceRepository(settings)
    profiles = ProfileRepository(settings)

    def _boom(_user_id: str) -> list[str]:
        raise RuntimeError("supabase down")

    devices.list_active_tokens = _boom  # type: ignore[method-assign]

    result = _service(settings, devices, profiles).notify_journal_ready(user_id=USER_ID, entry_id=ENTRY_ID)

    assert result.status == "failed"
    assert result.reason == "token_lookup_failed"
    assert fake_messaging.calls == []


def test_tokens_are_chunked_at_the_fcm_limit(fake_messaging: _FakeMessaging) -> None:
    settings = _settings(fcm_batch_size=2)
    devices = DeviceRepository(settings)
    profiles = ProfileRepository(settings)
    for index in range(5):
        devices.register_device(USER_ID, f"fcm-token-{index:04d}")

    result = _service(settings, devices, profiles).notify_journal_ready(user_id=USER_ID, entry_id=ENTRY_ID)

    assert [len(call["tokens"]) for call in fake_messaging.calls] == [2, 2, 1]
    assert result.sent == 5


def test_dry_run_is_passed_through(fake_messaging: _FakeMessaging) -> None:
    settings = _settings(firebase_dry_run=True)
    devices = DeviceRepository(settings)
    profiles = ProfileRepository(settings)
    devices.register_device(USER_ID, LIVE_TOKEN)

    _service(settings, devices, profiles).notify_journal_ready(user_id=USER_ID, entry_id=ENTRY_ID)

    assert fake_messaging.calls[0]["dry_run"] is True


def test_delivery_config_covers_android_and_ios(fake_messaging: _FakeMessaging) -> None:
    """One multicast targets both platforms, so it carries Android and APNs config."""
    settings = _settings()
    devices = DeviceRepository(settings)
    profiles = ProfileRepository(settings)
    devices.register_device(USER_ID, LIVE_TOKEN, platform="ios")

    _service(settings, devices, profiles).notify_journal_ready(user_id=USER_ID, entry_id=ENTRY_ID)

    call = fake_messaging.calls[0]
    assert call["tokens"] == [LIVE_TOKEN]
    assert call["android_priority"] == "high"
    assert call["apns_headers"] == {"apns-priority": "10"}


def test_classify_send_error_maps_unregistered_to_dead_token() -> None:
    assert classify_send_error(messaging, messaging.UnregisteredError("gone")) == SEND_ERROR_DEAD_TOKEN
    assert classify_send_error(messaging, messaging.SenderIdMismatchError("wrong project")) == SEND_ERROR_DEAD_TOKEN


def test_classify_send_error_maps_server_side_failures_to_retry() -> None:
    assert classify_send_error(messaging, messaging.QuotaExceededError("slow down")) == SEND_ERROR_RETRY
    assert classify_send_error(messaging, messaging.ThirdPartyAuthError("apns")) == SEND_ERROR_RETRY
    assert classify_send_error(messaging, exceptions.UnavailableError("try later")) == SEND_ERROR_RETRY
    assert classify_send_error(messaging, exceptions.InternalError("boom")) == SEND_ERROR_RETRY


def test_classify_send_error_does_not_treat_plain_not_found_as_dead_token() -> None:
    """`UnregisteredError` subclasses `NotFoundError`; a bare 404 is not a dead token.

    Both report `.code == "NOT_FOUND"`, so classifying on the code string would
    prune live registrations whenever the FCM endpoint 404s.
    """
    assert classify_send_error(messaging, exceptions.NotFoundError("project missing")) == SEND_ERROR_UNKNOWN


def test_classify_send_error_handles_unrecognised_and_empty_errors() -> None:
    assert classify_send_error(messaging, ValueError("nope")) == SEND_ERROR_UNKNOWN
    assert classify_send_error(messaging, None) == SEND_ERROR_UNKNOWN
