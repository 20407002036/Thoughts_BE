import asyncio
import io

import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from app.core.settings import Settings
from app.models.schemas import JournalAnalysis
from app.services.journal_pipeline import JournalPipeline
from app.services.transcription_service import TranscriptionError, TranscriptionService


class _StorageStub:
    def upload_audio(self, user_id: str, filename: str | None, content: bytes, content_type: str) -> tuple[str, str | None]:
        return f"{user_id}/stored.mp3", None


class _TranscriptionStub:
    def __init__(self, text: str = "Transcript text", error: Exception | None = None) -> None:
        self._text = text
        self._error = error

    def transcribe(self, filename: str | None, content: bytes) -> str:
        if self._error is not None:
            raise self._error
        return self._text


class _AnalysisStub:
    def analyze(self, transcript: str) -> JournalAnalysis:
        return JournalAnalysis(
            mood="calm",
            title="Title",
            summary=transcript,
            themes=["theme"],
            insights=["insight"],
        )


class _RepositoryStub:
    def create_entry(self, payload: dict[str, object]) -> dict[str, object]:
        return {
            **payload,
            "id": "entry-1",
            "created_at": "2026-04-14T00:00:00Z",
        }


def _upload_file(content_type: str, payload: bytes, filename: str = "entry.mp3") -> UploadFile:
    return UploadFile(
        file=io.BytesIO(payload),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


def _build_pipeline(settings: Settings, transcription_stub: _TranscriptionStub) -> JournalPipeline:
    return JournalPipeline(
        settings=settings,
        storage_service=_StorageStub(),
        transcription_service=transcription_stub,
        analysis_service=_AnalysisStub(),
        journal_repository=_RepositoryStub(),
    )


def test_week3_pipeline_stores_and_transcribes_mp3() -> None:
    pipeline = _build_pipeline(Settings(max_upload_mb=12), _TranscriptionStub(text="Today felt balanced"))

    result = asyncio.run(
        pipeline.process_upload(
            user_id="user-1",
            audio_file=_upload_file("audio/mpeg", b"abc123", "voice.mp3"),
        )
    )

    assert result.audio_path == "user-1/stored.mp3"
    assert result.transcript == "Today felt balanced"


def test_week3_pipeline_rejects_non_audio_content_type() -> None:
    pipeline = _build_pipeline(Settings(max_upload_mb=12), _TranscriptionStub())

    with pytest.raises(ValueError, match="Uploaded file must be an audio format"):
        asyncio.run(
            pipeline.process_upload(
                user_id="user-1",
                audio_file=_upload_file("text/plain", b"not-audio", "note.txt"),
            )
        )


def test_week3_pipeline_rejects_empty_upload() -> None:
    pipeline = _build_pipeline(Settings(max_upload_mb=12), _TranscriptionStub())

    with pytest.raises(ValueError, match="Uploaded audio file is empty"):
        asyncio.run(
            pipeline.process_upload(
                user_id="user-1",
                audio_file=_upload_file("audio/mpeg", b"", "empty.mp3"),
            )
        )


def test_week3_pipeline_enforces_max_upload_size() -> None:
    pipeline = _build_pipeline(Settings(max_upload_mb=0), _TranscriptionStub())

    with pytest.raises(ValueError, match="Audio exceeds max size"):
        asyncio.run(
            pipeline.process_upload(
                user_id="user-1",
                audio_file=_upload_file("audio/mpeg", b"1", "big.mp3"),
            )
        )


def test_week3_pipeline_surfaces_transcription_failure() -> None:
    pipeline = _build_pipeline(
        Settings(max_upload_mb=12),
        _TranscriptionStub(error=TranscriptionError("Failed to transcribe uploaded audio")),
    )

    with pytest.raises(TranscriptionError, match="Failed to transcribe uploaded audio"):
        asyncio.run(
            pipeline.process_upload(
                user_id="user-1",
                audio_file=_upload_file("audio/mpeg", b"abc123", "voice.mp3"),
            )
        )


def test_transcription_service_raises_on_provider_exception() -> None:
    settings = Settings(groq_api_key="test-key")
    service = TranscriptionService(settings)

    class _FailingTranscriptions:
        @staticmethod
        def create(*args: object, **kwargs: object) -> object:
            raise RuntimeError("provider down")

    class _FailingAudio:
        transcriptions = _FailingTranscriptions()

    class _FailingClient:
        audio = _FailingAudio()

    service._client = _FailingClient()  # type: ignore[assignment]

    with pytest.raises(TranscriptionError, match="Failed to transcribe uploaded audio"):
        service.transcribe(filename="voice.mp3", content=b"audio")


def test_transcription_service_raises_on_empty_transcript() -> None:
    settings = Settings(groq_api_key="test-key")
    service = TranscriptionService(settings)

    class _EmptyResult:
        text = "   "

    class _EmptyTranscriptions:
        @staticmethod
        def create(*args: object, **kwargs: object) -> object:
            return _EmptyResult()

    class _EmptyAudio:
        transcriptions = _EmptyTranscriptions()

    class _EmptyClient:
        audio = _EmptyAudio()

    service._client = _EmptyClient()  # type: ignore[assignment]

    with pytest.raises(TranscriptionError, match="Transcription returned empty text"):
        service.transcribe(filename="voice.mp3", content=b"audio")
