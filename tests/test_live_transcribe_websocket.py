import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.settings import get_settings
from app.main import app


def test_live_transcribe_websocket_requires_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
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
