from datetime import datetime, timezone
from fastapi.testclient import TestClient

from app.api.journals import _build_journal_repository, _build_journal_service, _build_pipeline
from app.api.recordings import _build_recording_pipeline
from app.core.security import AuthenticatedUser, get_current_user
from app.core.settings import get_settings
from app.main import app
from app.models.schemas import JournalAnalysis, JournalEntryResponse
from app.worker import process_journal_upload


def _dev_user() -> AuthenticatedUser:
    return AuthenticatedUser(user_id="async-user", email="async@example.com")


def _clear_cached_dependencies() -> None:
    get_settings.cache_clear()
    _build_pipeline.cache_clear()
    _build_journal_repository.cache_clear()
    _build_journal_service.cache_clear()
    _build_recording_pipeline.cache_clear()


def test_ingest_async_returns_202_and_persists_entry(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("INGEST_ASYNC", "true")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

    _clear_cached_dependencies()

    # Stub the Celery task's `delay` so the test exercises the API contract
    # (202 response shape, recording_id round-trip) without dragging Celery's
    # bind=True / asyncio.run() machinery into the test path. The stub persists
    # a real entry through the same repository the worker would use, so the
    # subsequent GET /v1/recordings/{id} finds it.
    repository = _build_journal_repository()

    def _fake_pipeline_sync(**kwargs):
        recording_id = kwargs["recording_id"]
        payload = {
            "id": recording_id,
            "user_id": kwargs["user_id"],
            "transcript": "stubbed transcript",
            "mood": "calm",
            "title": "Stubbed entry",
            "summary": "Pipeline was stubbed for the test.",
            "themes": [],
            "insights": [],
            "audio_path": kwargs.get("storage_path") or f"{kwargs['user_id']}/stub.mp3",
            "audio_signed_url": None,
            "prompt_version": "v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        saved = repository.create_entry(payload)
        return JournalEntryResponse(
            id=str(saved.get("id", recording_id)),
            user_id=saved.get("user_id", kwargs["user_id"]),
            transcript=saved.get("transcript", ""),
            analysis=JournalAnalysis(
                mood=saved.get("mood", "calm"),
                title=saved.get("title", "Stubbed entry"),
                summary=saved.get("summary", ""),
                themes=[],
                insights=[],
            ),
            audio_path=saved.get("audio_path", ""),
            audio_signed_url=saved.get("audio_signed_url"),
            prompt_version=saved.get("prompt_version", "v1"),
            created_at=datetime.fromisoformat(
                str(saved.get("created_at", payload["created_at"])).replace("Z", "+00:00")
            ),
        )

    monkeypatch.setattr(process_journal_upload, "delay", _fake_pipeline_sync)
    app.dependency_overrides[get_current_user] = _dev_user

    try:
        client = TestClient(app)
        ingest_response = client.post(
            "/v1/journals/ingest",
            files={"audio": ("journal.mp3", b"audio-bytes", "audio/mpeg")},
        )

        assert ingest_response.status_code == 202
        ingest_payload = ingest_response.json()
        assert ingest_payload["status"] == "processing"
        assert ingest_payload["progress_percent"] == 0
        assert ingest_payload["recording_id"]

        recording_id = ingest_payload["recording_id"]
        get_response = client.get(f"/v1/recordings/{recording_id}")
        assert get_response.status_code == 200
        get_payload = get_response.json()
        assert get_payload["recording_id"] == recording_id
        assert get_payload["status"] == "completed"
        assert get_payload["progress_percent"] == 100
        assert get_payload["entry_id"] == recording_id
    finally:
        app.dependency_overrides.clear()
        _clear_cached_dependencies()


def test_get_recording_reports_processing_when_entry_absent(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("INGEST_ASYNC", "true")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

    _clear_cached_dependencies()
    app.dependency_overrides[get_current_user] = _dev_user

    try:
        client = TestClient(app)
        response = client.get("/v1/recordings/missing-recording-id")

        assert response.status_code == 200
        payload = response.json()
        assert payload["recording_id"] == "missing-recording-id"
        assert payload["status"] == "processing"
        assert payload["progress_percent"] == 0
        assert payload["entry_id"] is None
    finally:
        app.dependency_overrides.clear()
        _clear_cached_dependencies()
