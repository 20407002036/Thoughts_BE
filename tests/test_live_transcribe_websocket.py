import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.settings import get_settings
from app.main import app


def test_live_transcribe_websocket_rejects_missing_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    get_settings.cache_clear()

    client = TestClient(app)
    try:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/v1/journals/live-transcribe"):
                pass
    finally:
        get_settings.cache_clear()

    assert exc_info.value.code == 1008


def test_live_transcribe_websocket_accepts_json_stop_command(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyLiveTranscriptionService:
        def __init__(self, settings):
            pass

        def create_recognizer(self):
            return object()

        def get_final_result(self, recognizer):
            return "complete text"

        @staticmethod
        def process_chunk(recognizer, audio_chunk):
            return {"partial": "", "final": None, "is_final": False}

    monkeypatch.setenv("AUTH_REQUIRED", "false")
    get_settings.cache_clear()
    monkeypatch.setattr("app.api.journals.LiveTranscriptionService", DummyLiveTranscriptionService)

    client = TestClient(app)
    try:
        with client.websocket_connect("/v1/journals/live-transcribe") as websocket:
            websocket.send_text('{  "action" : "stop" }')
            payload = websocket.receive_json()
    finally:
        get_settings.cache_clear()

    assert payload == {
        "final": "complete text",
        "is_final": True,
        "session_ended": True,
    }
