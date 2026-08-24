import logging
from uuid import uuid4

from celery import Celery

from app.core.settings import get_settings
from app.repositories.journal_repository import JournalRepository
from app.repositories.profile_repository import ProfileRepository
from app.services.analysis_service import AnalysisService
from app.services.journal_pipeline import JournalPipeline
from app.services.storage_service import StorageService
from app.services.streak_service import StreakService
from app.services.transcription_service import TranscriptionService

settings = get_settings()
logger = logging.getLogger(__name__)

# Initialize Celery
# We expect CELERY_BROKER_URL and CELERY_RESULT_BACKEND to be in the environment or settings
celery_app = Celery(
    "mindful_moments_worker",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

# Setup beat schedule for the "Reaper" task
celery_app.conf.beat_schedule = {
    "reset-expired-streaks-hourly": {
        "task": "worker.reset_streaks",
        "schedule": 3600.0,  # Every hour
    },
}


def _build_pipeline(user_id: str) -> JournalPipeline:
    """Construct a JournalPipeline with worker-local dependencies."""
    settings_local = get_settings()
    return JournalPipeline(
        settings=settings_local,
        storage_service=StorageService(settings_local),
        transcription_service=TranscriptionService(settings_local),
        analysis_service=AnalysisService(settings_local),
        journal_repository=JournalRepository(settings_local),
        streak_service=StreakService(ProfileRepository(settings_local)),
    )


@celery_app.task(name="worker.process_journal_upload", bind=True, max_retries=2, default_retry_delay=10)
def process_journal_upload(
    self,
    user_id: str,
    audio_path: str,
    content_type: str,
    recording_id: str | None = None,
    correlation_id: str | None = None,
) -> dict:
    """
    Async ingest task: download audio that the API process already uploaded to
    storage, then run the standard transcribe → analyze → persist → streak pipeline.

    Args:
        user_id: The authenticated user the entry belongs to.
        audio_path: Storage path returned by StorageService.upload_audio.
        content_type: Original MIME type from the request, used for the upload and
            the transcription call.
        recording_id: The id the API generated up front (so the 202 response is
            immediately useful to the client). If absent, a new uuid4 is generated.
        correlation_id: Propagated from the request so worker logs are joinable
            with API logs.
    """
    log_extra = {
        "user_id": user_id,
        "audio_path": audio_path,
        "recording_id": recording_id,
        "correlation_id": correlation_id,
    }
    logger.info("process_journal_upload_started", extra=log_extra)

    if not recording_id:
        recording_id = str(uuid4())
        logger.warning(
            "process_journal_upload_missing_recording_id",
            extra={**log_extra, "generated_recording_id": recording_id},
        )
        log_extra["recording_id"] = recording_id

    try:
        storage = StorageService(get_settings())
        audio_bytes = storage.download_audio(audio_path)
    except FileNotFoundError as exc:
        logger.error("process_journal_upload_audio_missing", extra=log_extra, exc_info=True)
        # No point retrying — the bytes are gone.
        return {"status": "failed", "error": "audio_not_found", "recording_id": recording_id}
    except Exception as exc:
        logger.exception("process_journal_upload_download_failed", extra=log_extra)
        raise self.retry(exc=exc) from exc

    pipeline = _build_pipeline(user_id)

    try:
        result = pipeline.run_pipeline_sync(
            user_id=user_id,
            audio_bytes=audio_bytes,
            filename=audio_path,
            content_type=content_type,
            recording_id=recording_id,
            storage_path=audio_path,
        )
    except Exception as exc:
        logger.exception("process_journal_upload_pipeline_failed", extra=log_extra)
        # Retry once for transient provider errors, then give up.
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {"status": "failed", "error": "pipeline_failed", "recording_id": recording_id}

    logger.info(
        "process_journal_upload_completed",
        extra={**log_extra, "entry_id": result.id},
    )
    return {
        "status": "completed",
        "recording_id": recording_id or result.id,
        "entry_id": result.id,
    }


@celery_app.task(name="worker.reset_streaks")
def reset_streaks():
    """
    Celery task to find users who haven't journaled in 48 hours
    and reset their streak to 0.
    """
    logger.info("Running scheduled streak reset task...")
    try:
        # Manually instantiate dependencies for the worker
        profile_repo = ProfileRepository(settings)
        streak_service = StreakService(profile_repo)

        reset_count = streak_service.reset_expired_streaks()
        logger.info(f"Streak reset task completed. Reset {reset_count} streaks.")
    except Exception:
        logger.exception("Failed to reset expired streaks")
        raise
    return True
