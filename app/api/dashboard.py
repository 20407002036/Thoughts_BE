from fastapi import APIRouter, Depends, HTTPException, status

from app.api.journals import get_journal_service
from app.api.profile import get_profile_service
from app.core.security import AuthenticatedUser, get_current_user
from app.models.schemas import DashboardSummary, ErrorResponse
from app.repositories.journal_repository import JournalRepositoryError
from app.repositories.profile_repository import ProfileRepositoryError
from app.services.journal_service import JournalService
from app.services.profile_service import ProfileService

router = APIRouter(prefix="/v1/dashboard", tags=["dashboard"])


@router.get(
    "",
    response_model=DashboardSummary,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
    },
)
def get_dashboard(
    current_user: AuthenticatedUser = Depends(get_current_user),
    journal_service: JournalService = Depends(get_journal_service),
    profile_service: ProfileService = Depends(get_profile_service),
) -> DashboardSummary:
    try:
        entries = journal_service.list_entries(
            user_id=current_user.user_id,
            limit=5,
            offset=0,
            month=None,
            query=None,
            tag=None,
        )
        profile = profile_service.get_profile(user_id=current_user.user_id, email=current_user.email)
    except (JournalRepositoryError, ProfileRepositoryError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return DashboardSummary(
        prompt=None,
        prompt_status="unavailable",
        recent_entries=entries.entries,
        streak_count=profile.get("streak_count", 0),
        entry_count=entries.total,
    )
