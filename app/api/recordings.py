import logging
from functools import lru_cache
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse

from app.api.journals import get_journal_service
from app.core.logging import get_correlation_id
from app.core.security import AuthenticatedUser, get_current_user
from app.core.settings import Settings, get_settings
from app.models.schemas import ErrorResponse, RecordingSessionResponse
from app.repositories.journal_repository import JournalRepositoryError
from app.services.analysis_service import AnalysisError, AnalysisService
from app.services.journal_pipeline import JournalPipeline, PipelineTimeoutError
from app.services.journal_service import JournalNotFoundError, JournalService
from app.services.storage_service import StorageService
from app.services.transcription_service import TranscriptionError, TranscriptionService
from app.api.journals import _build_journal_repository

router = APIRouter(prefix="/v1/recordings", tags=["recordings"])
logger = logging.getLogger(__name__)


@lru_cache
def _build_recording_pipeline() -> JournalPipeline:
    settings = get_settings()
    return JournalPipeline(
        settings=settings,
        storage_service=StorageService(settings),
        transcription_service=TranscriptionService(settings),
        analysis_service=AnalysisService(settings),
        journal_repository=_build_journal_repository(),
    )


def get_recording_pipeline(settings: Settings = Depends(get_settings)) -> JournalPipeline:
    _ = settings
    return _build_recording_pipeline()


@router.post(
    "",
    response_model=RecordingSessionResponse,
    responses={
        status.HTTP_202_ACCEPTED: {"model": RecordingSessionResponse},
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: {"model": ErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
        status.HTTP_504_GATEWAY_TIMEOUT: {"model": ErrorResponse},
    },
)
async def create_recording(
    audio: UploadFile = File(...),
    current_user: AuthenticatedUser = Depends(get_current_user),
    pipeline: JournalPipeline = Depends(get_recording_pipeline),
    settings: Settings = Depends(get_settings),
) -> RecordingSessionResponse:
    if settings.ingest_async:
        content_type = audio.content_type or ""
        if not content_type.startswith("audio/"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file must be an audio format",
            )

        content = await audio.read()
        if not content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded audio file is empty",
            )

        max_bytes = settings.max_upload_mb * 1024 * 1024
        if len(content) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Audio exceeds max size of {settings.max_upload_mb} MB",
            )

        recording_id = str(uuid4())
        storage = StorageService(settings)
        try:
            audio_path, _ = storage.upload_audio(
                user_id=current_user.user_id,
                filename=audio.filename,
                content=content,
                content_type=content_type,
            )
        except Exception as exc:
            logger.exception("recordings_storage_upload_failed")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to store audio upload",
            ) from exc

        from app.worker import process_journal_upload

        process_journal_upload.delay(
            user_id=current_user.user_id,
            audio_path=audio_path,
            content_type=content_type,
            recording_id=recording_id,
            correlation_id=get_correlation_id(),
        )

        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=RecordingSessionResponse(
                recording_id=recording_id,
                status="processing",
                progress_percent=0,
            ).model_dump(mode="json"),
        )

    try:
        entry = await pipeline.process_upload(user_id=current_user.user_id, audio_file=audio)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except (AnalysisError, TranscriptionError, JournalRepositoryError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except PipelineTimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc)) from exc

    return RecordingSessionResponse(
        recording_id=entry.id,
        status="completed",
        progress_percent=100,
        entry_id=entry.id,
    )


@router.get(
    "/{recording_id}",
    response_model=RecordingSessionResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
    },
)
def get_recording(
    recording_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    journal_service: JournalService = Depends(get_journal_service),
) -> RecordingSessionResponse:
    try:
        entry = journal_service.get_entry(user_id=current_user.user_id, entry_id=recording_id)
    except JournalNotFoundError:
        return RecordingSessionResponse(
            recording_id=recording_id,
            status="processing",
            progress_percent=0,
        )
    except JournalRepositoryError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return RecordingSessionResponse(
        recording_id=recording_id,
        status="completed",
        progress_percent=100,
        entry_id=entry.id,
    )
