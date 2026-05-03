from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.security import AuthenticatedUser, get_current_user
from app.core.settings import Settings, get_settings
from app.models.schemas import ErrorResponse, ProfileResponse, UpdateProfileRequest
from app.repositories.profile_repository import ProfileRepository, ProfileRepositoryError
from app.services.profile_service import ProfileService, ProfileValidationError

router = APIRouter(prefix="/v1/profile", tags=["profile"])


@lru_cache
def _build_profile_service() -> ProfileService:
    settings = get_settings()
    return ProfileService(profile_repository=ProfileRepository(settings=settings))


def get_profile_service(settings: Settings = Depends(get_settings)) -> ProfileService:
    _ = settings
    return _build_profile_service()


@router.get(
    "",
    response_model=ProfileResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
    },
)
def get_profile(
    current_user: AuthenticatedUser = Depends(get_current_user),
    profile_service: ProfileService = Depends(get_profile_service),
) -> ProfileResponse:
    try:
        payload = profile_service.get_profile(user_id=current_user.user_id, email=current_user.email)
    except ProfileRepositoryError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return ProfileResponse(**payload)


@router.patch(
    "",
    response_model=ProfileResponse,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
    },
)
def update_profile(
    payload: UpdateProfileRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    profile_service: ProfileService = Depends(get_profile_service),
) -> ProfileResponse:
    try:
        result = profile_service.update_display_name(
            user_id=current_user.user_id,
            email=current_user.email,
            display_name=payload.display_name,
        )
    except ProfileValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ProfileRepositoryError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return ProfileResponse(**result)
