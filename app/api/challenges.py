import logging
from functools import lru_cache

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.core.security import AuthenticatedUser, get_current_user
from app.core.settings import Settings, get_settings
from app.models.schemas import ChallengeAttemptResponse, ChallengeResponse, ErrorResponse
from app.repositories.challenge_repository import ChallengeRepository, ChallengeRepositoryError
from app.services.challenge_analysis_service import ChallengeAnalysisError, ChallengeAnalysisService
from app.services.challenge_pipeline import ChallengePipeline, ChallengePipelineError
from app.services.storage_service import StorageService
from app.services.transcription_service import TranscriptionService

router = APIRouter(prefix="/v1/challenges", tags=["challenges"])
logger = logging.getLogger(__name__)


@lru_cache
def _build_challenge_repository() -> ChallengeRepository:
    return ChallengeRepository(get_settings())


@lru_cache
def _build_pipeline() -> ChallengePipeline:
    settings = get_settings()
    return ChallengePipeline(
        settings=settings,
        storage_service=StorageService(settings),
        transcription_service=TranscriptionService(settings),
        challenge_analysis_service=ChallengeAnalysisService(settings),
        challenge_repository=_build_challenge_repository(),
    )


def get_pipeline(settings: Settings = Depends(get_settings)) -> ChallengePipeline:
    _ = settings
    return _build_pipeline()


def get_repository(settings: Settings = Depends(get_settings)) -> ChallengeRepository:
    _ = settings
    return _build_challenge_repository()


@router.get(
    "",
    response_model=list[ChallengeResponse],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
    },
)
def list_challenges(
    current_user: AuthenticatedUser = Depends(get_current_user),
    repository: ChallengeRepository = Depends(get_repository),
) -> list[ChallengeResponse]:
    _ = current_user
    try:
        data = repository.list_challenges()
        return [ChallengeResponse(**c) for c in data]
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve challenges",
        ) from exc


@router.get(
    "/{challenge_id}",
    response_model=ChallengeResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
    },
)
def get_challenge(
    challenge_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    repository: ChallengeRepository = Depends(get_repository),
) -> ChallengeResponse:
    _ = current_user
    try:
        challenge = repository.get_challenge(challenge_id)
        if not challenge:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Challenge with ID {challenge_id} not found",
            )
        return ChallengeResponse(**challenge)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve challenge",
        ) from exc


@router.post(
    "/{challenge_id}/attempt",
    response_model=ChallengeAttemptResponse,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
        status.HTTP_504_GATEWAY_TIMEOUT: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
    },
)
async def attempt_challenge(
    challenge_id: str,
    audio: UploadFile = File(...),
    duration_seconds: float | None = Form(default=None),
    current_user: AuthenticatedUser = Depends(get_current_user),
    pipeline: ChallengePipeline = Depends(get_pipeline),
) -> ChallengeAttemptResponse:
    try:
        return await pipeline.process_attempt(
            user_id=current_user.user_id,
            challenge_id=challenge_id,
            audio_file=audio,
            duration_seconds=duration_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ChallengeAnalysisError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except ChallengeRepositoryError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except ChallengePipelineError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected processing failure",
        ) from exc
