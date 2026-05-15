from datetime import datetime, timezone
from typing import Any

from postgrest.exceptions import APIError
from supabase import Client, create_client

from app.core.settings import Settings


class ProfileRepositoryError(RuntimeError):
    """Raised when profile persistence fails."""


class ProfileRepository:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Client | None = None
        self._local_profiles: dict[str, dict[str, Any]] = {}

        if settings.supabase_url and settings.supabase_service_role_key:
            self._client = create_client(settings.supabase_url, settings.supabase_service_role_key)

    def _default_profile(self, user_id: str) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        return {
            "id": user_id,
            "display_name": None,
            "avatar_url": None,
            "timezone": "UTC",
            "tagline": None,
            "streak_count": 0,
            "last_journal_saved": None,
            "notifications_enabled": True,
            "prompt_reminder_time": None,
            "appearance_mode": "system",
            "audio_quality": "standard",
            "language": "en",
            "encryption_status": "managed",
            "created_at": now,
            "updated_at": now,
        }

    def get_profile(self, user_id: str) -> dict[str, Any]:
        if self._client is None:
            profile = self._local_profiles.get(user_id)
            if profile is None:
                profile = self._default_profile(user_id)
                self._local_profiles[user_id] = profile
            return profile

        # Use an atomic upsert so concurrent first access cannot race on
        # select-then-insert and fail with a duplicate key error.
        try:
            result = (
                self._client.table(self._settings.supabase_profiles_table)
                .upsert({"id": user_id}, on_conflict="id")
                .execute()
            )
        except APIError as exc:
            raise ProfileRepositoryError(f"Supabase profile upsert failed: {exc.message}") from exc

        rows = result.data or []
        if rows:
            return rows[0]

        # If the mutation response does not include the row, read it back.
        try:
            select_result = (
                self._client.table(self._settings.supabase_profiles_table)
                .select("*")
                .eq("id", user_id)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            raise ProfileRepositoryError(f"Supabase profile select failed: {exc.message}") from exc

        selected_rows = select_result.data or []
        if not selected_rows:
            raise ProfileRepositoryError("Supabase profile upsert returned no row")
        return selected_rows[0]

    def update_display_name(self, user_id: str, display_name: str) -> dict[str, Any]:
        if self._client is None:
            profile = self._local_profiles.get(user_id) or self._default_profile(user_id)
            profile["display_name"] = display_name
            profile["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._local_profiles[user_id] = profile
            return profile

        try:
            result = (
                self._client.table(self._settings.supabase_profiles_table)
                .upsert({"id": user_id, "display_name": display_name}, on_conflict="id")
                .execute()
            )
        except APIError as exc:
            raise ProfileRepositoryError(f"Supabase profile upsert failed: {exc.message}") from exc

        rows = result.data or []
        if not rows:
            raise ProfileRepositoryError("Supabase profile upsert returned no row")
        return rows[0]

    def get_preferences(self, user_id: str) -> dict[str, Any]:
        profile = self.get_profile(user_id=user_id)
        return self._preferences_from_profile(profile)

    def update_preferences(self, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "notifications_enabled",
            "prompt_reminder_time",
            "appearance_mode",
            "audio_quality",
            "language",
        }
        update_payload = {key: value for key, value in payload.items() if key in allowed}

        if self._client is None:
            profile = self._local_profiles.get(user_id) or self._default_profile(user_id)
            profile.update(update_payload)
            profile["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._local_profiles[user_id] = profile
            return self._preferences_from_profile(profile)

        try:
            result = (
                self._client.table(self._settings.supabase_profiles_table)
                .upsert({"id": user_id, **update_payload}, on_conflict="id")
                .execute()
            )
        except APIError as exc:
            raise ProfileRepositoryError(f"Supabase preferences upsert failed: {exc.message}") from exc

        rows = result.data or []
        if not rows:
            raise ProfileRepositoryError("Supabase preferences upsert returned no row")
        return self._preferences_from_profile(rows[0])

    def update_streak(self, user_id: str, streak_count: int, last_journal_saved: str) -> dict[str, Any]:
        if self._client is None:
            profile = self._local_profiles.get(user_id) or self._default_profile(user_id)
            profile["streak_count"] = streak_count
            profile["last_journal_saved"] = last_journal_saved
            profile["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._local_profiles[user_id] = profile
            return profile

        try:
            result = (
                self._client.table(self._settings.supabase_profiles_table)
                .upsert({"id": user_id, "streak_count": streak_count, "last_journal_saved": last_journal_saved}, on_conflict="id")
                .execute()
            )
        except APIError as exc:
            raise ProfileRepositoryError(f"Supabase streak update failed: {exc.message}") from exc

        rows = result.data or []
        if not rows:
            raise ProfileRepositoryError("Supabase streak update returned no row")
        return rows[0]

    def get_profiles_inactive_since(self, cutoff_time: str) -> list[dict[str, Any]]:
        """Returns profiles where last_journal_saved is older than cutoff_time."""
        if self._client is None:
            return [p for p in self._local_profiles.values()
                    if p.get("last_journal_saved") and p["last_journal_saved"] < cutoff_time]

        try:
            result = (
                self._client.table(self._settings.supabase_profiles_table)
                .select("id, streak_count")
                .lt("last_journal_saved", cutoff_time)
                .gt("streak_count", 0)
                .execute()
            )
        except APIError as exc:
            raise ProfileRepositoryError(f"Supabase inactive profiles query failed: {exc.message}") from exc

        return result.data or []

    def reset_streak(self, user_id: str) -> dict[str, Any]:
        return self.update_streak(user_id, 0, datetime.now(timezone.utc).isoformat())
