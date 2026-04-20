from fastapi.testclient import TestClient

from app.api.journals import _build_pipeline, get_pipeline
from app.core.security import AuthenticatedUser, get_current_user
from app.core.settings import get_settings
from app.main import app


def _dev_user() -> AuthenticatedUser:
    return AuthenticatedUser(user_id="week6-user", email="week6@example.com")


def test_week6_ingest_end_to_end_local_fallback(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    get_settings.cache_clear()

    _build_pipeline.cache_clear()
    app.dependency_overrides[get_current_user] = _dev_user
    app.dependency_overrides[get_pipeline] = _build_pipeline

    try:
        client = TestClient(app)
        response = client.post(
            "/v1/journals/ingest",
            headers={"X-Correlation-ID": "week6-corr-id"},
            files={"audio": ("journal.mp3", b"audio-bytes", "audio/mpeg")},
        )
    finally:
        app.dependency_overrides.clear()
        _build_pipeline.cache_clear()
        get_settings.cache_clear()

    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] == "week6-corr-id"

    payload = response.json()
    assert payload["id"]
    assert payload["user_id"] == "week6-user"
    assert payload["transcript"] == "Local mode transcription placeholder"
    assert payload["audio_signed_url"] is None
    assert payload["audio_path"].startswith("week6-user/")
    assert payload["prompt_version"] == "v1"
    assert payload["created_at"]

    analysis = payload["analysis"]
    assert analysis["mood"] == "reflective"
    assert analysis["title"] == "Local Draft Entry"
    assert analysis["themes"] == ["journal", "reflection"]
    assert analysis["insights"] == ["Connect Groq API key to enable model analysis"]


def test_week6_ready_endpoint_contract_shape() -> None:
    client = TestClient(app)
    response = client.get("/ready")

    assert response.status_code == 200
    payload = response.json()

    assert payload["status"] == "ready"
    assert isinstance(payload["environment"], str)
    assert isinstance(payload["auth_required"], bool)
    assert isinstance(payload["supabase_configured"], bool)
    assert isinstance(payload["groq_configured"], bool)
