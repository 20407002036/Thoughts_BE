from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from postgrest.exceptions import APIError
from supabase import Client, create_client

from app.core.settings import Settings


class JournalRepositoryError(RuntimeError):
    """Raised when journal persistence fails."""


class JournalRepository:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Client | None = None
        self._local_entries: list[dict[str, Any]] = []

        if settings.supabase_url and settings.supabase_service_role_key:
            self._client = create_client(settings.supabase_url, settings.supabase_service_role_key)

    def create_entry(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._client is None:
            entry = {
                **payload,
                "id": str(uuid4()),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._local_entries.append(entry)
            return entry

        try:
            result = self._client.table(self._settings.supabase_journals_table).insert(payload).execute()
        except APIError as exc:
            details: list[str] = [f"code={exc.code}"] if exc.code else []
            if exc.details:
                details.append(f"details={exc.details}")
            if exc.hint:
                details.append(f"hint={exc.hint}")

            detail_suffix = f" ({'; '.join(details)})" if details else ""
            raise JournalRepositoryError(f"Supabase insert failed: {exc.message}{detail_suffix}") from exc

        data = result.data or []
        if not data:
            raise JournalRepositoryError("Supabase insert returned no row")
        return data[0]
