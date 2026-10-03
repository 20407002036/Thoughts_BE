"""End-to-end smoke test for event-driven push, without Firebase or a device.

Exercises the real code path a notification takes in production — token
registration, the `notifications_enabled` gate, the pipeline's push stage, and
the exact FCM payload — against a stubbed messaging module. Nothing is
delivered and no credentials are needed, so this is runnable before the
Firebase project exists.

Usage:
    python -m scripts.push_smoke

What it proves:
  1. POST /v1/devices stores a token and is idempotent.
  2. The ingest pipeline dispatches a push after the entry is persisted.
  3. The payload carries the entry id in `data` and no journal text.
  4. notifications_enabled=false suppresses delivery without unregistering.
  5. A dead token (UnregisteredError) is deactivated and never retried.
  6. `data` is exactly what the Flutter app will receive on tap.

What it does NOT prove: that Google accepts the payload, and that a real
device displays it. Only FIREBASE_DRY_RUN plus POST /v1/devices/test covers
that.
"""
from __future__ import annotations

import os
import sys

USER_ID = "smoke-user"

TOKEN = "fake-fcm-token-for-smoke-test-0001"

# Importing this module must have no side effects. A previous version of this
# file was named push_smoke_test.py, which matched pytest's default
# python_files pattern (*_test.py) and was therefore imported during test
# collection. Its module-level os.environ.setdefault calls then leaked
# FIREBASE_ENABLED=true into every test in the suite, which broke
# tests/test_push_dispatch.py. Nothing here may touch os.environ or app state
# at import time.
_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        _failures.append(label)


def _configure_env() -> None:
    """Local fallback mode: no Supabase, sync ingest, FCM on but stubbed."""
    os.environ.setdefault("SUPABASE_URL", "")
    os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "")
    os.environ.setdefault("GROQ_API_KEY", "")
    os.environ.setdefault("AUTH_REQUIRED", "true")
    os.environ.setdefault("INGEST_ASYNC", "false")
    # The ingest endpoint 503s when the limiter is disabled, so it must be on
    # here; _stub_rate_limiter() supplies the Redis it needs.
    os.environ.setdefault("RATE_LIMIT_ENABLED", "true")
    os.environ.setdefault("FIREBASE_ENABLED", "true")
    os.environ.setdefault("FIREBASE_PROJECT_ID", "smoke-test-project")


def _build_stub_messaging(messaging):
    """Return a stand-in for firebase_admin.messaging that records sends."""

    class _StubMessaging:
        Notification = messaging.Notification
        AndroidConfig = messaging.AndroidConfig
        APNSConfig = messaging.APNSConfig
        MulticastMessage = messaging.MulticastMessage
        UnregisteredError = messaging.UnregisteredError
        SenderIdMismatchError = messaging.SenderIdMismatchError
        QuotaExceededError = messaging.QuotaExceededError
        ThirdPartyAuthError = messaging.ThirdPartyAuthError

        def __init__(self) -> None:
            self.sent: list[dict[str, object]] = []
            self.dead_tokens: set[str] = set()

        def send_each_for_multicast(self, message, dry_run: bool = False):
            sent_now: list[dict[str, object]] = []

            class _Response:
                def __init__(self, success: bool, exception: Exception | None = None) -> None:
                    self.success = success
                    self.exception = exception

            responses = []
            for token in message.tokens:
                sent_now.append(
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
                if token in self.dead_tokens:
                    responses.append(_Response(False, exception=messaging.UnregisteredError("token not registered")))
                else:
                    responses.append(_Response(True))

            self.sent.extend(sent_now)
            return type("_BatchResponse", (), {"responses": responses})()

    return _StubMessaging()


def main() -> int:
    _configure_env()

    from firebase_admin import messaging

    from fastapi.testclient import TestClient

    from app.api import journals as journals_api
    from app.api.devices import get_device_repository, get_push_service
    from app.core import rate_limit as rate_limit_module
    from app.core.security import AuthenticatedUser, get_current_user
    from app.core.settings import get_settings
    from app.main import app
    from app.repositories.device_repository import DeviceRepository
    from app.repositories.profile_repository import ProfileRepository
    from app.services import push_service as push_module

    get_settings.cache_clear()
    settings = get_settings()

    print("\n1. Configuration")
    check("firebase_configured reports true", settings.firebase_configured)

    stub = _build_stub_messaging(messaging)
    push_module._get_messaging_module = lambda: stub  # type: ignore[assignment]
    push_module.initialize_firebase_app = lambda _s: object()  # type: ignore[assignment]

    class _FakePipeline:
        def incr(self, _key: str) -> None:
            _ = None

        def expire(self, _key: str, _seconds: int) -> None:
            _ = None

        async def execute(self) -> list[int]:
            return [1]

    class _FakeRedis:
        def pipeline(self, transaction: bool = False) -> _FakePipeline:
            _ = transaction
            return _FakePipeline()

    # The ingest endpoint hard-requires the rate limiter (it 503s when
    # disabled) and the limiter needs Redis. This script is about the push
    # path, so the limiter is stubbed to always allow.
    rate_limit_module._get_redis_client = lambda *_a, **_k: _FakeRedis()  # type: ignore[assignment]

    devices = DeviceRepository(settings)
    profiles = ProfileRepository(settings)
    push = push_module.PushService(
        settings=settings,
        device_repository=devices,
        profile_repository=profiles,
    )

    client = TestClient(app)
    app.dependency_overrides.update(
        {
            get_device_repository: lambda: devices,
            get_push_service: lambda: push,
        }
    )
    # The pipeline's own push dispatcher resolves its PushService through a
    # cached factory. Left alone it would build a second instance backed by a
    # second in-memory device store and find no tokens, so point it at the
    # same service this script uses and drop the cached pipeline.
    journals_api._build_push_service = lambda: push  # type: ignore[assignment]
    journals_api._build_pipeline.cache_clear()

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id=USER_ID, email="smoke@example.com")

    try:
        print("\n2. POST /v1/devices")
        first = client.post("/v1/devices", json={"token": TOKEN, "platform": "android", "app_version": "1.0.0"})
        check("registration returns 201", first.status_code == 201, f"got {first.status_code}")
        again = client.post("/v1/devices", json={"token": TOKEN, "platform": "android"})
        check("re-registration is idempotent", again.json().get("device_id") == first.json().get("device_id"))
        check("token is active", devices.list_active_tokens(USER_ID) == [TOKEN])

        print("\n3. Ingest pipeline dispatches a push")
        stub.sent.clear()
        response = client.post(
            "/v1/journals/ingest",
            files={"audio": ("journal.mp3", b"fake-audio-bytes", "audio/mpeg")},
        )
        check("ingest succeeded", response.status_code == 200, f"got {response.status_code}: {response.text[:120]}")
        entry_id = response.json().get("id", "")
        check("a push was dispatched", len(stub.sent) == 1, f"{len(stub.sent)} dispatch(es)")

        if stub.sent:
            message = stub.sent[0]
            print("\n4. Payload the Flutter app receives")
            print(f"     notification.title = {message['title']!r}")
            print(f"     notification.body  = {message['body']!r}")
            print(f"     data               = {message['data']!r}")
            print(f"     android.priority   = {message['android_priority']!r}")
            print(f"     apns.headers       = {message['apns_headers']!r}")
            print()
            check("data.type is journal_ready", message["data"].get("type") == "journal_ready")
            check("data.entry_id matches the saved entry", message["data"].get("entry_id") == entry_id)
            check("entry id is not in the title", entry_id not in str(message["title"]))
            check("entry id is not in the body", entry_id not in str(message["body"]))
            check("no journal text in the body", "fake-audio-bytes" not in str(message["body"]))
            check("android priority is high", message["android_priority"] == "high")
            check("apns priority is set", message["apns_headers"] == {"apns-priority": "10"})

        print("\n5. Opting out stops delivery but keeps the registration")
        profile = profiles.get_profile(USER_ID)
        profile["notifications_enabled"] = False
        profiles._local_profiles[USER_ID] = profile
        stub.sent.clear()
        opted_out = push.notify_journal_ready(user_id=USER_ID, entry_id=entry_id)
        check("status is skipped", opted_out.status == "skipped")
        check("reason is notifications_disabled", opted_out.reason == "notifications_disabled")
        check("nothing was sent", not stub.sent)
        check("device is still registered", devices.list_active_tokens(USER_ID) == [TOKEN])

        profile["notifications_enabled"] = True
        profiles._local_profiles[USER_ID] = profile

        print("\n6. A dead token is retired")
        stub.dead_tokens.add(TOKEN)
        stub.sent.clear()
        dead = push.notify_journal_ready(user_id=USER_ID, entry_id=entry_id)
        check("send was attempted", dead.failed == 1)
        check("token was deactivated", dead.deactivated == 1)
        check("token is no longer active", devices.list_active_tokens(USER_ID) == [])

        print("\n7. Unregistering a device")
        devices.register_device(USER_ID, TOKEN)
        removed = client.delete(f"/v1/devices/{TOKEN}")
        check("returns 200", removed.status_code == 200)
        check("reports removal", removed.json().get("removed") is True)
    finally:
        app.dependency_overrides.clear()

    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s) — {', '.join(_failures)}")
        return 1
    print("All checks passed.")
    print("\nNot covered here: real FCM delivery and on-device display.")
    print("Next: set FIREBASE_ENABLED with real credentials and FIREBASE_DRY_RUN=true,")
    print("then call POST /v1/devices/test from the mobile app.")
    return 0


if __name__ == "__main__":
    sys.exit(main())