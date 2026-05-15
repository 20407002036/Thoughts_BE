import asyncio
from datetime import datetime, timezone
import logging

from fastapi import UploadFile

from app.core.settings import Settings
from app.models.schemas import JournalEntryResponse
from app.repositories.journal_repository import JournalRepository
from app.services.analysis_service import AnalysisService
from app.services.storage_service import StorageService
from app.services.transcription_service import TranscriptionService

logger = logging.getLogger(__name__)


class PipelineTimeoutError(RuntimeError):
    """Raised when a pipeline stage exceeds configured timeout."""


class JournalPipeline:
    def __init__(
        self,
        settings: Settings,
        storage_service: StorageService,
        transcription_service: TranscriptionService,
        analysis_service: AnalysisService,
        journal_repository: JournalRepository,
        streak_service: Any,
    ) -> None:
        self._settings = settings
        self._storage = storage_service
        self._transcription = transcription_service
        self._analysis = analysis_service
        self._repository = journal_repository
        self._streak_service = streak_service

    async def process_upload(self, user_id: str, audio_file: UploadFile) -> JournalEntryResponse:
        content_type = audio_file.content_type or ""
        if not content_type.startswith("audio/"):
            raise ValueError("Uploaded file must be an audio format")

        content = await audio_file.read()
        if not content:
            raise ValueError("Uploaded audio file is empty")

        max_bytes = self._settings.max_upload_mb * 1024 * 1024
        if len(content) > max_bytes:
            raise ValueError(f"Audio exceeds max size of {self._settings.max_upload_mb} MB")

        audio_path, signed_url = await self._run_with_timeout(
            "upload_audio",
            self._storage.upload_audio,
            user_id=user_id,
            filename=audio_file.filename,
            content=content,
            content_type=content_type,
        )

        transcript = await self._run_with_timeout(
            "transcribe",
            self._transcription.transcribe,
            filename=audio_file.filename,
            content=content,
        )
        analysis = await self._run_with_timeout("analyze", self._analysis.analyze, transcript)

        payload = {
            "user_id": user_id,
            "transcript": transcript,
            "mood": analysis.mood,
            "title": analysis.title,
            "summary": analysis.summary,
            "themes": analysis.themes,
            "insights": analysis.insights,
            "audio_path": audio_path,
            "audio_signed_url": signed_url,
            "prompt_version": self._settings.analysis_prompt_version,
        }
        saved = await self._run_with_timeout("create_entry", self._repository.create_entry, payload)

        created_at_raw = saved.get("created_at")
        if created_at_raw:
            created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
        else:
            created_at = datetime.now(timezone.utc)

        # Update streak for the day
        self._streak_service.update_streak(user_id=user_id)

        return JournalEntryResponse(
            id=str(saved.get("id", "")),
            user_id=saved.get("user_id", user_id),
            transcript=saved.get("transcript", transcript),
            analysis=analysis,
            audio_path=saved.get("audio_path", audio_path),
            audio_signed_url=saved.get("audio_signed_url", signed_url),
            prompt_version=saved.get("prompt_version", self._settings.analysis_prompt_version),
            created_at=created_at,
        )

    async def _run_with_timeout(self, stage: str, func, *args, **kwargs):
        timeout_seconds = self._settings.request_timeout_seconds
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(func, *args, **kwargs),
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            logger.warning(
                "pipeline_stage_timeout",
                extra={"stage": stage, "timeout_seconds": timeout_seconds},
            )
            raise PipelineTimeoutError(f"Pipeline stage '{stage}' timed out") from exc
