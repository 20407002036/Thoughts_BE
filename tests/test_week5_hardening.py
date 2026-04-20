import asyncio
import io
import time
from datetime import datetime, timezone

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from app.api.journals import get_pipeline
from app.core.security import AuthenticatedUser, get_current_user
from app.core.settings import Settings
from app.main import app
from app.models.schemas import JournalAnalysis, JournalEntryResponse
from app.services.journal_pipeline import JournalPipeline, PipelineTimeoutError


class _TimeoutPipeline:
    async def process_upload(self, user_id: str, audio_file: object) -> JournalEntryResponse:
        raise PipelineTimeoutError("Pipeline stage 'transcribe' timed out")


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


class _StorageSlowStub:
    def upload_audio(self, user_id: str, filename: str | None, content: bytes, content_type: str) -> tuple[str, str | None]:
        time.sleep(0.05)
        return f"{user_id}/stored.mp3", None


class _TranscriptionStub:
    def transcribe(self, filename: str | None, content: bytes) -> str:
        return "Transcript text"


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


def test_ingest_uses_consistent_error_envelope_and_correlation_id() -> None:
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="user-1", email="user@example.com")
    app.dependency_overrides[get_pipeline] = lambda: _TimeoutPipeline()

    try:
        client = TestClient(app)
        response = client.post(
            "/v1/journals/ingest",
            headers={"X-Correlation-ID": "corr-123"},
            files={"audio": ("voice.mp3", b"audio-bytes", "audio/mpeg")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 504
    assert response.headers["X-Correlation-ID"] == "corr-123"
    payload = response.json()
    assert payload["error"] == "request_timeout"
    assert payload["message"] == "Pipeline stage 'transcribe' timed out"
    assert payload["correlation_id"] == "corr-123"


def test_ingest_maps_unexpected_failure_to_internal_error() -> None:
    class _ExplodingPipeline:
        async def process_upload(self, user_id: str, audio_file: object) -> JournalEntryResponse:
            raise RuntimeError("boom")

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user_id="user-1", email="user@example.com")
    app.dependency_overrides[get_pipeline] = lambda: _ExplodingPipeline()

    try:
        client = TestClient(app)
        response = client.post(
            "/v1/journals/ingest",
            files={"audio": ("voice.mp3", b"audio-bytes", "audio/mpeg")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    payload = response.json()
    assert payload["error"] == "internal_error"
    assert payload["message"] == "Unexpected processing failure"
    assert payload["correlation_id"]


def test_ingest_adds_generated_correlation_id_header_on_success() -> None:
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
    assert response.headers["X-Correlation-ID"]


def test_pipeline_raises_timeout_when_stage_exceeds_limit() -> None:
    pipeline = JournalPipeline(
        settings=Settings(max_upload_mb=12, request_timeout_seconds=0),
        storage_service=_StorageSlowStub(),
        transcription_service=_TranscriptionStub(),
        analysis_service=_AnalysisStub(),
        journal_repository=_RepositoryStub(),
    )

    with pytest.raises(PipelineTimeoutError, match="upload_audio"):
        asyncio.run(
            pipeline.process_upload(
                user_id="user-1",
                audio_file=_upload_file("audio/mpeg", b"abc123", "voice.mp3"),
            )
        )
