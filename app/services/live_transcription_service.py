import json
import logging
from pathlib import Path

from vosk import KaldiRecognizer, Model

from app.core.settings import Settings

logger = logging.getLogger(__name__)


class LiveTranscriptionError(RuntimeError):
    """Raised when live transcription cannot be initialized or processed."""


class LiveTranscriptionService:
    """
    Real-time transcription service using Vosk.
    Processes audio chunks as they arrive and returns partial + final results.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model: Model | None = None
        self._recognizer: KaldiRecognizer | None = None
        self._initialized = False

        if settings.vosk_model_path:
            self._initialize_model()

    def _initialize_model(self) -> None:
        """Initialize Vosk model from disk."""
        if not self._settings.vosk_model_path:
            raise LiveTranscriptionError("Vosk model path not configured")

        model_path = Path(self._settings.vosk_model_path)
        if not model_path.exists():
            raise LiveTranscriptionError(f"Vosk model not found at {model_path}")

        try:
            self._model = Model(str(model_path))
            self._initialized = True
        except Exception as exc:
            logger.exception("Failed to load Vosk model")
            raise LiveTranscriptionError("Failed to initialize Vosk model") from exc

    def create_recognizer(self, sample_rate: int = 16000) -> KaldiRecognizer:
        """Create a new recognizer for a transcription session."""
        if not self._initialized or not self._model:
            raise LiveTranscriptionError("Model not initialized")

        try:
            return KaldiRecognizer(self._model, sample_rate)
        except Exception as exc:
            logger.exception("Failed to create recognizer")
            raise LiveTranscriptionError("Failed to create recognizer") from exc

    @staticmethod
    def process_chunk(recognizer: KaldiRecognizer, audio_chunk: bytes) -> dict[str, object]:
        """
        Process a single audio chunk and return transcription result.

        Returns:
            {
                "partial": "partial transcription text" or None,
                "final": "final transcription text" or None,
                "is_final": bool,
            }
        """
        result = {"partial": None, "final": None, "is_final": False}

        try:
            if recognizer.AcceptWaveform(audio_chunk):
                # Final result
                final_json = recognizer.Result()
                final_data = json.loads(final_json)
                result["final"] = final_data.get("text")
                result["is_final"] = True
            else:
                # Partial result
                partial_json = recognizer.PartialResult()
                partial_data = json.loads(partial_json)
                result["partial"] = partial_data.get("partial")
        except Exception as exc:
            logger.exception("Error processing audio chunk")
            raise LiveTranscriptionError("Failed to process audio chunk") from exc

        if logger.isEnabledFor(logging.DEBUG):
            if result["is_final"]:
                logger.debug("Live transcription final: %s", result["final"])
            else:
                logger.debug("Live transcription partial: %s", result["partial"])
        return result

    def get_final_result(self, recognizer: KaldiRecognizer) -> str:
        """Get the recognizer's final result for the current session."""
        try:
            final_json = recognizer.Result()
            final_data = json.loads(final_json)
            text = final_data.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()

            result_list = final_data.get("result", [])
            if isinstance(result_list, list) and result_list:
                return " ".join(
                    item.get("word", "")
                    for item in result_list
                    if isinstance(item, dict) and isinstance(item.get("word", ""), str)
                ).strip()

            return ""
        except Exception as exc:
            logger.exception("Error getting final result")
            return ""
