from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.journals import get_pipeline
from app.core.security import AuthenticatedUser, get_current_user
from app.core.settings import Settings
from app.main import app
from app.models.schemas import JournalAnalysis, JournalEntryResponse
from app.services.analysis_service import AnalysisError, AnalysisService


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _EmptyFakeResponse:
	choices: list[object] = []


class _FakeCompletions:
    def __init__(self, content: str | None = None, error: Exception | None = None, empty: bool = False) -> None:
        self._content = content
        self._error = error
        self._empty = empty

    def create(self, *args: object, **kwargs: object) -> _FakeResponse:
        if self._error is not None:
            raise self._error
        if self._empty:
            return _EmptyFakeResponse()
        return _FakeResponse(self._content or "{}")


class _FakeChat:
    def __init__(self, content: str | None = None, error: Exception | None = None, empty: bool = False) -> None:
        self.completions = _FakeCompletions(content=content, error=error, empty=empty)


class _FakeGroqClient:
    def __init__(self, content: str | None = None, error: Exception | None = None, empty: bool = False) -> None:
        self.chat = _FakeChat(content=content, error=error, empty=empty)


class _FailingAnalysisPipeline:
    async def process_upload(self, user_id: str, audio_file: object) -> JournalEntryResponse:
        raise AnalysisError("Model response did not match expected analysis schema")


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


def test_analysis_service_raises_on_malformed_json_schema() -> None:
    service = AnalysisService(Settings(groq_api_key="test-key"))
    service._client = _FakeGroqClient(content='{"mood":"calm"}')  # type: ignore[assignment]

    with pytest.raises(AnalysisError, match="Model response did not match expected analysis schema"):
        service.analyze("Some transcript")


def test_analysis_service_raises_on_provider_exception() -> None:
    service = AnalysisService(Settings(groq_api_key="test-key"))
    service._client = _FakeGroqClient(error=RuntimeError("provider down"))  # type: ignore[assignment]

    with pytest.raises(AnalysisError, match="Failed to analyze transcript"):
        service.analyze("Some transcript")


def test_analysis_service_raises_on_empty_model_response() -> None:
    service = AnalysisService(Settings(groq_api_key="test-key"))
    service._client = _FakeGroqClient(empty=True)  # type: ignore[assignment]

    with pytest.raises(AnalysisError, match="Model response did not match expected analysis schema"):
        service.analyze("Some transcript")


def test_ingest_maps_analysis_error_to_502() -> None:
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="user-1", email="user@example.com")
    app.dependency_overrides[get_pipeline] = lambda: _FailingAnalysisPipeline()

    try:
        client = TestClient(app)
        response = client.post(
            "/v1/journals/ingest",
            files={"audio": ("voice.mp3", b"audio-bytes", "audio/mpeg")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    payload = response.json()
    assert payload["error"] == "upstream_error"
    assert payload["message"] == "Model response did not match expected analysis schema"
    assert payload["correlation_id"]


def test_ingest_maps_repository_error_to_502() -> None:
    from app.repositories.journal_repository import JournalRepositoryError

    class _FailingRepositoryPipeline:
        async def process_upload(self, user_id: str, audio_file: object) -> JournalEntryResponse:
            raise JournalRepositoryError("Supabase insert returned no row")

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="user-1", email="user@example.com")
    app.dependency_overrides[get_pipeline] = lambda: _FailingRepositoryPipeline()

    try:
        client = TestClient(app)
        response = client.post(
            "/v1/journals/ingest",
            files={"audio": ("voice.mp3", b"audio-bytes", "audio/mpeg")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    payload = response.json()
    assert payload["error"] == "upstream_error"
    assert payload["message"] == "Supabase insert returned no row"
    assert payload["correlation_id"]


def test_ingest_returns_complete_entry_response() -> None:
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

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "entry-1"
    assert payload["user_id"] == "user-1"
    assert payload["transcript"] == "Today felt balanced"
    assert payload["audio_path"] == "user-1/entry.mp3"
    assert payload["audio_signed_url"] is None
    assert payload["prompt_version"] == "v1"
    assert payload["created_at"] == "2026-04-15T00:00:00Z"
    assert payload["analysis"] == {
        "mood": "calm",
        "title": "Steady Day",
        "summary": "A calm and focused day.",
        "themes": ["balance"],
        "insights": ["Breathing helped reset focus."],
    }
