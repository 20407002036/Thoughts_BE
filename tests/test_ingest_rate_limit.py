import asyncio
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.api.journals import get_pipeline
from app.api.recordings import get_recording_pipeline
from app.core.rate_limit import check_rate_limit
from app.core.security import AuthenticatedUser, get_current_user
from app.core.settings import get_settings
from app.main import app
from app.models.schemas import JournalAnalysis, JournalEntryResponse


class _SuccessPipeline:
    async def process_upload(self, user_id: str, audio_file: object) -> JournalEntryResponse:
        return JournalEntryResponse(
            id="entry-1",
            user_id=user_id,
            transcript="Today felt balanced",
            analysis=JournalAnalysis(
                mood="calm",
                title="Steady Day",
                summary="A calm and focused day.",
                themes=["balance"],
                insights=["Breathing helped reset focus."],
            ),
            audio_path="user-1/entry.mp3",
            audio_signed_url=None,
            prompt_version="v1",
            created_at=datetime(2026, 4, 15, tzinfo=timezone.utc),
        )


def test_journals_ingest_returns_429_when_rate_limited(monkeypatch) -> None:
    async def _deny(*args: object, **kwargs: object) -> tuple[bool, int]:
        return False, 7

    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr("app.core.rate_limit.check_rate_limit", _deny)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="user-1", email="user@example.com")
    app.dependency_overrides[get_pipeline] = lambda: _SuccessPipeline()

    try:
        client = TestClient(app)
        response = client.post(
            "/v1/journals/ingest",
            files={"audio": ("voice.mp3", b"audio-bytes", "audio/mpeg")},
        )
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "7"
    assert response.json()["message"] == "Rate limit exceeded. Try again in 7 seconds."


def test_recordings_ingest_returns_429_when_rate_limited(monkeypatch) -> None:
    async def _deny(*args: object, **kwargs: object) -> tuple[bool, int]:
        return False, 9

    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr("app.core.rate_limit.check_rate_limit", _deny)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="user-1", email="user@example.com")
    app.dependency_overrides[get_recording_pipeline] = lambda: _SuccessPipeline()

    try:
        client = TestClient(app)
        response = client.post(
            "/v1/recordings",
            files={"audio": ("voice.mp3", b"audio-bytes", "audio/mpeg")},
        )
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "9"
    assert response.json()["message"] == "Rate limit exceeded. Try again in 9 seconds."


def test_check_rate_limit_fails_open_when_runtime_error_occurs() -> None:
    class _FakePipeline:
        def incr(self, key: str) -> None:
            _ = key

        def expire(self, key: str, ttl: int) -> None:
            _ = key, ttl

        async def execute(self) -> list[int]:
            raise RuntimeError("Event loop is closed")

    class _FakeRedisClient:
        def pipeline(self, transaction: bool = False) -> _FakePipeline:
            _ = transaction
            return _FakePipeline()

    allowed, retry_after = asyncio.run(
        check_rate_limit(
            _FakeRedisClient(),
            key_prefix="ingest_min",
            user_id="user-1",
            limit=5,
            window_seconds=60,
        )
    )

    assert allowed is True
    assert retry_after == 0
