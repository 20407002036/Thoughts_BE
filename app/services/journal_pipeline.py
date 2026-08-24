import asyncio
from datetime import datetime, timezone
import logging
from uuid import uuid4

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
        streak_service: object | None = None,
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

        return await self.run_pipeline(
            user_id=user_id,
            audio_bytes=content,
            filename=audio_file.filename or "audio",
            content_type=content_type,
        )

    async def run_pipeline(
        self,
        user_id: str,
        audio_bytes: bytes,
        filename: str,
        content_type: str,
        recording_id: str | None = None,
        storage_path: str | None = None,
        storage_signed_url: str | None = None,
    ) -> JournalEntryResponse:
        """Run transcribe → analyze → persist → streak for already-loaded audio bytes.

        Used by both the synchronous API ingest path (after `process_upload` reads
        the UploadFile) and the Celery worker (which downloads bytes from storage
        by path before calling this).
        """
        uploaded_by_pipeline = storage_path is None
        audio_path = None
        try:
            if uploaded_by_pipeline:
                audio_path, signed_url = await self._run_with_timeout(
                    "upload_audio",
                    self._storage.upload_audio,
                    user_id=user_id,
                    filename=filename,
                    content=audio_bytes,
                    content_type=content_type,
                )
            else:
                audio_path = storage_path
                signed_url = storage_signed_url
                if signed_url is None:
                    signed_url = await self._run_with_timeout(
                        "signed_url",
                        self._storage.signed_url_for_path,
                        storage_path,
                    )

            transcript = await self._run_with_timeout(
                "transcribe",
                self._transcription.transcribe,
                filename=filename,
                content=audio_bytes,
            )
            # Audio bytes are no longer needed after transcription — release the
            # reference so the GC can reclaim the (potentially large) buffer while
            # the rest of the pipeline (analysis, persist, streak) runs.
            del audio_bytes

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
                "prompt_version": self._settings.analysis_prompt_version,
            }
            payload["id"] = recording_id if recording_id else str(uuid4())

            saved = await self._run_with_timeout("create_entry", self._repository.create_entry, payload)
        except Exception:
            # If the pipeline uploaded the audio and a later stage failed,
            # delete the orphaned blob so storage doesn't accumulate
            # unreferenced files.
            if uploaded_by_pipeline and audio_path:
                try:
                    await self._run_with_timeout(
                        "cleanup_storage",
                        self._storage.delete_audio,
                        audio_path,
                    )
                except Exception:
                    logger.warning(
                        "storage_cleanup_failed",
                        extra={"audio_path": audio_path, "user_id": user_id},
                        exc_info=True,
                    )
            raise

        created_at_raw = saved.get("created_at")
        if created_at_raw:
            created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
        else:
            created_at = datetime.now(timezone.utc)

        # Update streak for the day as a best-effort follow-up so a saved
        # journal entry is not reported as a failed request if the streak
        # update cannot be persisted.
        try:
            await self._run_with_timeout(
                "update_streak",
                self._streak_service.update_streak,
                user_id=user_id,
            )
        except Exception:
            logger.warning(
                "streak_update_failed",
                extra={"user_id": user_id, "journal_id": str(saved.get("id", ""))},
                exc_info=True,
            )

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

    def run_pipeline_sync(
        self,
        user_id: str,
        audio_bytes: bytes,
        filename: str,
        content_type: str,
        recording_id: str | None = None,
        storage_path: str | None = None,
        storage_signed_url: str | None = None,
    ) -> JournalEntryResponse:
        """Synchronous entry point for the Celery worker.

        The pipeline core is async because the API request handler awaits it; the
        worker process is synchronous (Celery tasks aren't coroutines), so this
        wrapper drives the same `run_pipeline` coroutine from a fresh event loop.
        """
        return asyncio.run(
            self.run_pipeline(
                user_id=user_id,
                audio_bytes=audio_bytes,
                filename=filename,
                content_type=content_type,
                recording_id=recording_id,
                storage_path=storage_path,
                storage_signed_url=storage_signed_url,
            )
        )
