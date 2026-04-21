from typing import Any

from app.repositories.profile_repository import ProfileRepository, ProfileRepositoryError


class ProfileValidationError(RuntimeError):
    """Raised when profile input is invalid."""


class ProfileService:
    def __init__(self, profile_repository: ProfileRepository) -> None:
        self._profile_repository = profile_repository

    @staticmethod
    def _normalize_display_name(display_name: str) -> str:
        normalized = display_name.strip()
        if not normalized:
            raise ProfileValidationError("Display name cannot be blank")
        if len(normalized) > 100:
            raise ProfileValidationError("Display name must be at most 100 characters")
        return normalized

    def get_profile(self, user_id: str, email: str | None) -> dict[str, Any]:
        profile = self._profile_repository.get_profile(user_id=user_id)
        return {
            "user_id": user_id,
            "email": email,
            "display_name": profile.get("display_name"),
            "streak_count": profile.get("streak_count") or 0,
            "last_journal_saved": profile.get("last_journal_saved"),
            "created_at": profile.get("created_at"),
            "updated_at": profile.get("updated_at"),
        }

    def update_display_name(self, user_id: str, email: str | None, display_name: str) -> dict[str, Any]:
        normalized_name = self._normalize_display_name(display_name)
        profile = self._profile_repository.update_display_name(user_id=user_id, display_name=normalized_name)
        return {
            "user_id": user_id,
            "email": email,
            "display_name": profile.get("display_name"),
            "streak_count": profile.get("streak_count") or 0,
            "last_journal_saved": profile.get("last_journal_saved"),
            "created_at": profile.get("created_at"),
            "updated_at": profile.get("updated_at"),
        }
