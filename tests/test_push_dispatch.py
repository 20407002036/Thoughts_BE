"""Event-driven push wiring: the pipeline dispatch stage and the worker task.

Covers both ingest paths:

* the synchronous API path, which calls PushService directly through
  `app.api.journals._dispatch_push`;
* the async worker path, which enqueues the `worker.send_push` Celery task.
"""
from typing import Any

import pytest

from app.api import journals as journals_api
from app.core.settings import Settings
from app.models.schemas import JournalAnalysis
from app.repositories.device_repository import DeviceRepository
from app.repositories.profile_repository import ProfileRepository
from app.services.journal_pipeline import JournalPipeline
from app.services.push_service import KIND_JOURNAL_READY, PushResult, PushService
from app.worker import _enqueue_push, send_push

USER_ID = "user-1"
ENTRY_ID = "entry-1"


class _StubStorage:
    def upload_audio(self, user_id: str, filename: str | None, content: bytes, content_type: str) -> tuple[str, None]:
        return f"{user_id}/entry.mp3", None

    def signed_url_for_path(self, path: str) -> None:
        return None

    def delete_audio(self, path: str) -> None:
        return None


class _StubTranscription:
    def transcribe(self, filename: str, content: bytes) -> str:
        return "Today felt calm and steady."


class _StubAnalysis:
    def analyze(self, transcript: str) -> JournalAnalysis:
        return JournalAnalysis(
            mood="calm",
            title="A steady day",
            summary="A calm reflection.",
            themes=[],
            insights=[],
        )


class _StubRepository:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    def create_entry(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.created.append(payload)
        return {**payload, "id": payload["id"], "created_at": "2026-05-20T10:00:00+00:00"}


class _StubStreakService:
    def __init__(self) -> None:
        self.updated: list[str] = []

    def update_streak(self, user_id: str) -> None:
        self.updated.append(user_id)


class _StubProfileRepository:
    def __init__(self) -> None:
        self.profiles: dict[str, dict[str, Any]] = {}

    def get_profile(self, user_id: str) -> dict[str, Any]:
        return self.profiles.setdefault(user_id, {"id": user_id, "timezone": "UTC", "streak_count": 0})

    def update_streak(self, user_id: str, streak_count: int, last_journal_saved: str | None = None) -> dict[str, Any]:
        profile = self.get_profile(user_id)
        profile["streak_count"] = streak_count
        return profile


def _pipeline(
    settings: Settings,
    repository: _StubRepository,
    push_dispatcher=None,
    streak_service: _StubStreakService | None = None,
) -> JournalPipeline:
    return JournalPipeline(
        settings=settings,
        storage_service=_StubStorage(),
        transcription_service=_StubTranscription(),
        analysis_service=_StubAnalysis(),
        journal_repository=repository,  # type: ignore[arg-type]
        streak_service=streak_service or _StubStreakService(),
        push_dispatcher=push_dispatcher,
    )


def _run(pipeline: JournalPipeline) -> Any:
    return pipeline.run_pipeline_sync(
        user_id=USER_ID,
        audio_bytes=b"audio",
        filename="entry.mp3",
        content_type="audio/mpeg",
        recording_id=ENTRY_ID,
    )


def test_pipeline_invokes_the_push_dispatcher_after_persisting() -> None:
    calls: list[dict[str, Any]] = []
    repository = _StubRepository()
    settings = Settings(request_timeout_seconds=5)

    _run(_pipeline(settings, repository, push_dispatcher=lambda **kwargs: calls.append(kwargs)))

    assert repository.created, "entry must be persisted before the push is dispatched"
    assert calls == [{"user_id": USER_ID, "entry_id": ENTRY_ID}]


def test_pipeline_survives_a_failing_push_dispatcher() -> None:
    """A push failure must not turn a saved journal into a failed ingest."""
    repository = _StubRepository()

    def _boom(**_kwargs: Any) -> None:
        raise RuntimeError("firebase unavailable")

    result = _run(_pipeline(Settings(request_timeout_seconds=5), repository, push_dispatcher=_boom))

    assert result.id == ENTRY_ID
    assert repository.created


def test_pipeline_skips_the_push_stage_when_no_dispatcher_is_wired() -> None:
    repository = _StubRepository()

    result = _run(_pipeline(Settings(request_timeout_seconds=5), repository))

    assert result.id == ENTRY_ID


def test_api_dispatch_push_calls_push_service(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, Any]] = []

    class _StubPushService:
        def notify_journal_ready(self, user_id: str, entry_id: str) -> PushResult:
            sent.append({"user_id": user_id, "entry_id": entry_id})
            return PushResult(status="sent", sent=1)

    monkeypatch.setattr(journals_api, "_build_push_service", lambda: _StubPushService())

    journals_api._dispatch_push(USER_ID, ENTRY_ID)

    assert sent == [{"user_id": USER_ID, "entry_id": ENTRY_ID}]


def test_enqueue_push_calls_the_celery_task(monkeypatch: pytest.MonkeyPatch) -> None:
    enqueued: list[dict[str, Any]] = []
    monkeypatch.setattr(send_push, "delay", lambda **kwargs: enqueued.append(kwargs))

    _enqueue_push(USER_ID, ENTRY_ID, correlation_id="corr-1")

    assert enqueued == [
        {
            "user_id": USER_ID,
            "kind": KIND_JOURNAL_READY,
            "entry_id": ENTRY_ID,
            "correlation_id": "corr-1",
        }
    ]


def test_enqueue_push_absorbs_broker_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broker outage must not fail the ingest that already saved the entry."""

    def _boom(**_kwargs: Any) -> None:
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr(send_push, "delay", _boom)

    _enqueue_push(USER_ID, ENTRY_ID)


def test_send_push_task_delivers_the_journal_ready_notification(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, str]] = []

    class _StubPushService:
        def notify_journal_ready(self, user_id: str, entry_id: str) -> PushResult:
            sent.append((user_id, entry_id))
            return PushResult(status="sent", sent=1)

    monkeypatch.setattr("app.worker._build_push_service", lambda: _StubPushService())

    result = send_push(user_id=USER_ID, kind=KIND_JOURNAL_READY, entry_id=ENTRY_ID, correlation_id="corr-1")

    assert sent == [(USER_ID, ENTRY_ID)]
    assert result == {"status": "sent", "sent": 1, "failed": 0, "deactivated": 0, "reason": None}


def test_send_push_task_skips_unknown_kinds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.worker._build_push_service", lambda: pytest.fail("must not build a push service"))

    result = send_push(user_id=USER_ID, kind="not_a_real_kind")

    assert result == {"status": "skipped", "reason": "unknown_kind"}


def test_send_push_task_reports_skipped_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    class _StubPushService:
        def notify_journal_ready(self, user_id: str, entry_id: str) -> PushResult:
            return PushResult(status="skipped", reason="no_active_devices")

    monkeypatch.setattr("app.worker._build_push_service", lambda: _StubPushService())

    result = send_push(user_id=USER_ID, kind=KIND_JOURNAL_READY, entry_id=ENTRY_ID)

    assert result["status"] == "skipped"
    assert result["reason"] == "no_active_devices"


def test_worker_pipeline_enqueues_the_push_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """The worker's pipeline is wired to the Celery task, not to a direct send."""
    import app.worker as worker_module

    enqueued: list[dict[str, Any]] = []
    monkeypatch.setattr(send_push, "delay", lambda **kwargs: enqueued.append(kwargs))
    monkeypatch.setattr(worker_module, "StorageService", lambda _settings: _StubStorage())
    monkeypatch.setattr(worker_module, "TranscriptionService", lambda _settings: _StubTranscription())
    monkeypatch.setattr(worker_module, "AnalysisService", lambda _settings: _StubAnalysis())
    monkeypatch.setattr(worker_module, "JournalRepository", lambda _settings: _StubRepository())
    monkeypatch.setattr(worker_module, "ProfileRepository", lambda _settings: _StubProfileRepository())
    monkeypatch.setattr(worker_module, "get_settings", lambda: Settings(supabase_url=None, supabase_service_role_key=None))

    _run(worker_module._build_pipeline(USER_ID, "corr-9"))

    assert enqueued == [
        {
            "user_id": USER_ID,
            "kind": KIND_JOURNAL_READY,
            "entry_id": ENTRY_ID,
            "correlation_id": "corr-9",
        }
    ]


def test_devices_and_profiles_share_the_worker_fallback_local_mode() -> None:
    """Sanity check that both repos stay in local-fallback mode with no Supabase."""
    settings = Settings(supabase_url=None, supabase_service_role_key=None)
    devices = DeviceRepository(settings)
    profiles = ProfileRepository(settings)
    devices.register_device(USER_ID, "fcm-token-abcdefghijklmnop", platform="ios")

    service = PushService(settings=settings, device_repository=devices, profile_repository=profiles)
    result = service.notify_journal_ready(user_id=USER_ID, entry_id=ENTRY_ID)

    assert result.status == "skipped"
    assert result.reason == "firebase_not_configured"
    assert devices.list_active_tokens(USER_ID) == ["fcm-token-abcdefghijklmnop"]
