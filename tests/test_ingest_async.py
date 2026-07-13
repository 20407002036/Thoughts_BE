from fastapi.testclient import TestClient

from app.api.journals import _build_journal_repository, _build_journal_service, _build_pipeline
from app.api.recordings import _build_recording_pipeline
from app.core.security import AuthenticatedUser, get_current_user
from app.core.settings import get_settings
from app.main import app
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

    def _run_task_immediately(**kwargs):
        process_journal_upload.run(**kwargs)

    monkeypatch.setattr(process_journal_upload, "delay", _run_task_immediately)
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
