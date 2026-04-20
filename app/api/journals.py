from functools import lru_cache

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.core.security import AuthenticatedUser, get_current_user
from app.core.settings import Settings, get_settings
from app.models.schemas import ErrorResponse, JournalEntryResponse
from app.repositories.journal_repository import JournalRepository, JournalRepositoryError
from app.services.analysis_service import AnalysisError, AnalysisService
from app.services.journal_pipeline import JournalPipeline, PipelineTimeoutError
from app.services.storage_service import StorageService
from app.services.transcription_service import TranscriptionError, TranscriptionService

router = APIRouter(prefix="/v1/journals", tags=["journals"])


@lru_cache
def _build_pipeline() -> JournalPipeline:
    settings = get_settings()
    return JournalPipeline(
        settings=settings,
        storage_service=StorageService(settings),
        transcription_service=TranscriptionService(settings),
        analysis_service=AnalysisService(settings),
        journal_repository=JournalRepository(settings),
    )


def get_pipeline(settings: Settings = Depends(get_settings)) -> JournalPipeline:
    # Include settings dependency so future env reload strategy can invalidate cache if needed.
    _ = settings
    return _build_pipeline()


@router.post(
    "/ingest",
    response_model=JournalEntryResponse,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
        status.HTTP_504_GATEWAY_TIMEOUT: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
    },
)
async def ingest_journal_audio(
    audio: UploadFile = File(...),
    current_user: AuthenticatedUser = Depends(get_current_user),
    pipeline: JournalPipeline = Depends(get_pipeline),
) -> JournalEntryResponse:
    try:
        return await pipeline.process_upload(user_id=current_user.user_id, audio_file=audio)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except AnalysisError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except TranscriptionError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except JournalRepositoryError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except PipelineTimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected processing failure",
        ) from exc
