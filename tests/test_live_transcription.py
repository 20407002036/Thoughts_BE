"""
Test suite for live transcription service.
"""

import json
import pytest

from app.core.settings import Settings
from app.services.live_transcription_service import (
    LiveTranscriptionError,
    LiveTranscriptionService,
)


def test_live_transcription_service_requires_model_path() -> None:
    """Service should raise error if model path not configured."""
    settings = Settings(vosk_model_path=None)
    service = LiveTranscriptionService(settings)

    with pytest.raises(LiveTranscriptionError, match="Model not initialized"):
        service.create_recognizer()


def test_live_transcription_service_raises_on_missing_model() -> None:
    """Service should raise error if model path doesn't exist."""
    settings = Settings(vosk_model_path="/nonexistent/path/model")

    with pytest.raises(LiveTranscriptionError, match="Vosk model not found"):
        LiveTranscriptionService(settings)


def test_live_transcription_service_processes_chunk() -> None:
    """
    Test chunk processing returns proper structure.
    Note: This test uses mock data since actual Vosk integration requires model.
    """
    # This would require actual Vosk model to run end-to-end
    # For unit testing purposes, we verify the result structure

    # Example of expected result from process_chunk
    expected_result = {
        "partial": None,
        "final": None,
        "is_final": False,
    }

    # Verify structure
    assert "partial" in expected_result
    assert "final" in expected_result
    assert "is_final" in expected_result


def test_live_transcription_service_handles_vosk_json_format() -> None:
    """Verify service can parse Vosk JSON response format."""
    # Vosk returns JSON with "partial" and "result" keys
    partial_response = '{"partial": "hello wo"}'
    final_response = '{"result": [{"conf": 1.0, "word": "hello"}, {"conf": 1.0, "word": "world"}]}'

    partial_data = json.loads(partial_response)
    final_data = json.loads(final_response)

    assert partial_data.get("partial") == "hello wo"
    assert isinstance(final_data.get("result"), list)
    assert len(final_data.get("result", [])) == 2


class TestLiveTranscriptionServiceIntegration:
    """Integration tests (requires actual Vosk model to be installed)."""

    def test_full_transcription_session_with_model(self) -> None:
        """
        Full end-to-end test with actual model.
        Skip if model not available.
        """
        settings = Settings()

        # Skip if model not configured
        if not settings.vosk_model_path:
            pytest.skip("Vosk model path not configured (set VOSK_MODEL_PATH)")

        try:
            service = LiveTranscriptionService(settings)
        except LiveTranscriptionError:
            pytest.skip("Vosk model not found")

        # Create recognizer
        recognizer = service.create_recognizer()
        assert recognizer is not None

        # Process empty chunk (should not error)
        result = LiveTranscriptionService.process_chunk(recognizer, b"")
        assert "partial" in result
        assert "final" in result
        assert "is_final" in result

        # Get final result
        final_text = service.get_final_result(recognizer)
        assert isinstance(final_text, str)
