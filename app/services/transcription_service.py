import io
import logging

from groq import Groq

from app.core.settings import Settings

logger = logging.getLogger(__name__)


class TranscriptionError(RuntimeError):
    """Raised when transcription cannot be completed successfully."""


class TranscriptionService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Groq | None = Groq(api_key=settings.groq_api_key) if settings.groq_api_key else None

    def transcribe(self, filename: str | None, content: bytes) -> str:
        if self._client is None:
            return "Local mode transcription placeholder"

        upload_name = filename or "journal_audio.mp3"
        try:
            result = self._client.audio.transcriptions.create(
                file=(upload_name, io.BytesIO(content)),
                model=self._settings.groq_whisper_model,
            )
        except Exception as exc:
            logger.exception("Groq transcription request failed")
            raise TranscriptionError("Failed to transcribe uploaded audio") from exc

        transcript = (getattr(result, "text", "") or "").strip()
        if not transcript:
            raise TranscriptionError("Transcription returned empty text")
        return transcript
