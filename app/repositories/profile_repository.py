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
            "streak_count": 0,
            "last_journal_saved": None,
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

        try:
            result = (
                self._client.table(self._settings.supabase_profiles_table)
                .select("*")
                .eq("id", user_id)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            raise ProfileRepositoryError(f"Supabase profile select failed: {exc.message}") from exc

        rows = result.data or []
        if rows:
            return rows[0]

        # Ensure consistent behavior with local mode when no row exists yet.
        try:
            insert_result = (
                self._client.table(self._settings.supabase_profiles_table)
                .insert({"id": user_id})
                .execute()
            )
        except APIError as exc:
            raise ProfileRepositoryError(f"Supabase profile insert failed: {exc.message}") from exc

        inserted_rows = insert_result.data or []
        if not inserted_rows:
            raise ProfileRepositoryError("Supabase profile insert returned no row")
        return inserted_rows[0]

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
