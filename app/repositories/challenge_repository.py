from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from postgrest.exceptions import APIError
from supabase import Client, create_client

from app.core.settings import Settings


class ChallengeRepositoryError(RuntimeError):
    """Raised when challenge persistence fails."""


STATIC_CHALLENGES = [
    {
        "id": "c76f6b55-d142-4f36-96b6-85777085fb0b",
        "title": "The Power of the Pause",
        "description": "Read the text below, but force yourself to pause for a full 2 seconds at every comma or period. Replace filler words with silence.",
        "difficulty": "beginner",
        "prompt_text": "Public speaking is not about the words we say. It is about the spaces between them. If you can master silence, you can master the room.",
        "vocal_goal": "Eliminate filler words completely using intentional 2-second pauses.",
        "time_limit_seconds": 60,
    },
    {
        "id": "d87f7c66-e253-5e47-a7c7-96888196ec1c",
        "title": "Vocal Variety (Rate Change)",
        "description": "Read the first sentence slowly to convey seriousness, and the second sentence quickly to convey enthusiasm.",
        "difficulty": "intermediate",
        "prompt_text": "This is a serious matter. We need to act immediately and with full energy!",
        "vocal_goal": "Contrast the slow first sentence with the energetic and fast second sentence.",
        "time_limit_seconds": 45,
    }
]


class ChallengeRepository:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Client | None = None
        self._local_attempts: list[dict[str, Any]] = []

        if settings.supabase_url and settings.supabase_service_role_key:
            self._client = create_client(settings.supabase_url, settings.supabase_service_role_key)

    def list_challenges(self) -> list[dict[str, Any]]:
        if self._client is None:
            return STATIC_CHALLENGES

        try:
            result = self._client.table(self._settings.supabase_challenges_table).select("*").execute()
            data = result.data or []
            if not data:
                # Fallback to static challenges if database is empty for easy onboarding
                return STATIC_CHALLENGES
            return data
        except APIError as exc:
            logger_warning = f"Supabase challenges select failed, returning static fallback: {exc.message}"
            return STATIC_CHALLENGES

    def get_challenge(self, challenge_id: str) -> dict[str, Any] | None:
        # Check static catalog first (robust fallback)
        challenge = next((c for c in STATIC_CHALLENGES if str(c["id"]) == challenge_id), None)
        if challenge:
            return challenge

        if self._client is None:
            return None

        try:
            result = (
                self._client.table(self._settings.supabase_challenges_table)
                .select("*")
                .eq("id", challenge_id)
                .limit(1)
                .execute()
            )
            data = result.data or []
            return data[0] if data else None
        except APIError as exc:
            raise ChallengeRepositoryError(f"Supabase challenge select failed: {exc.message}") from exc

    def create_attempt(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._client is None:
            attempt = {
                **payload,
                "id": str(uuid4()),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._local_attempts.append(attempt)
            return attempt

        try:
            result = self._client.table(self._settings.supabase_challenge_attempts_table).insert(payload).execute()
        except APIError as exc:
            raise ChallengeRepositoryError(f"Supabase attempt insert failed: {exc.message}") from exc

        data = result.data or []
        if not data:
            raise ChallengeRepositoryError("Supabase attempt insert returned no row")
        return data[0]

    def list_attempts(self, user_id: str) -> list[dict[str, Any]]:
        if self._client is None:
            rows = [a for a in self._local_attempts if a.get("user_id") == user_id]
            rows.sort(key=lambda a: str(a.get("created_at", "")), reverse=True)
            return rows

        try:
            result = (
                self._client.table(self._settings.supabase_challenge_attempts_table)
                .select("*")
                .eq("user_id", user_id)
                .order("created_at", desc=True)
                .execute()
            )
            return result.data or []
        except APIError as exc:
            raise ChallengeRepositoryError(f"Supabase attempts list failed: {exc.message}") from exc
