from fastapi import APIRouter, Depends, HTTPException, status

from app.api.profile import _build_profile_repository
from app.core.security import AuthenticatedUser, get_current_user
from app.models.schemas import ErrorResponse, UpdatePreferencesRequest, UserPreferences
from app.repositories.profile_repository import ProfileRepository, ProfileRepositoryError

router = APIRouter(prefix="/v1/preferences", tags=["preferences"])


def get_profile_repository() -> ProfileRepository:
    return _build_profile_repository()


@router.get(
    "",
    response_model=UserPreferences,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
    },
)
def get_preferences(
    current_user: AuthenticatedUser = Depends(get_current_user),
    profile_repository: ProfileRepository = Depends(get_profile_repository),
) -> UserPreferences:
    try:
        return UserPreferences(**profile_repository.get_preferences(user_id=current_user.user_id))
    except ProfileRepositoryError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.patch(
    "",
    response_model=UserPreferences,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
    },
)
def update_preferences(
    payload: UpdatePreferencesRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    profile_repository: ProfileRepository = Depends(get_profile_repository),
) -> UserPreferences:
    try:
        updates = payload.model_dump(exclude_unset=True)
        return UserPreferences(**profile_repository.update_preferences(user_id=current_user.user_id, payload=updates))
    except ProfileRepositoryError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
