from functools import lru_cache

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.api.journals import get_journal_service
from app.core.rate_limit import ingest_rate_limit
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
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_429_TOO_MANY_REQUESTS: {"model": ErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
        status.HTTP_504_GATEWAY_TIMEOUT: {"model": ErrorResponse},
    },
)
async def create_recording(
    audio: UploadFile = File(...),
    current_user: AuthenticatedUser = Depends(get_current_user),
    _rate_limited: AuthenticatedUser = Depends(ingest_rate_limit()),
    pipeline: JournalPipeline = Depends(get_recording_pipeline),
) -> RecordingSessionResponse:
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
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
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
    except JournalNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recording not found") from exc
    except JournalRepositoryError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return RecordingSessionResponse(
        recording_id=recording_id,
        status="completed",
        progress_percent=100,
        entry_id=entry.id,
    )
