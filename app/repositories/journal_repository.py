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
                "id": str(payload.get("id") or uuid4()),
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

    def list_entries(
        self,
        user_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
        month: str | None = None,
        query: str | None = None,
        tag: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        if self._client is None:
            rows = [entry for entry in self._local_entries if entry.get("user_id") == user_id]
            rows = self._filter_local_entries(rows, month=month, query=query, tag=tag)
            rows.sort(key=lambda entry: str(entry.get("created_at", "")), reverse=True)
            return rows[offset : offset + limit], len(rows)

        try:
            request = (
                self._client.table(self._settings.supabase_journals_table)
                .select("*", count="exact")
                .eq("user_id", user_id)
                .order("created_at", desc=True)
                .range(offset, offset + limit - 1)
            )
            if month:
                request = request.gte("created_at", f"{month}-01").lt("created_at", self._next_month(month))
            if query:
                search = f"%{query}%"
                request = request.or_(f"title.ilike.{search},summary.ilike.{search},transcript.ilike.{search}")
            if tag:
                request = request.contains("themes", [tag])

            result = request.execute()
        except APIError as exc:
            raise JournalRepositoryError(f"Supabase journal list failed: {exc.message}") from exc

        return result.data or [], result.count or 0

    def get_entry(self, user_id: str, entry_id: str) -> dict[str, Any] | None:
        if self._client is None:
            return next(
                (
                    entry
                    for entry in self._local_entries
                    if entry.get("user_id") == user_id and str(entry.get("id")) == entry_id
                ),
                None,
            )

        try:
            result = (
                self._client.table(self._settings.supabase_journals_table)
                .select("*")
                .eq("user_id", user_id)
                .eq("id", entry_id)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            raise JournalRepositoryError(f"Supabase journal select failed: {exc.message}") from exc

        rows = result.data or []
        return rows[0] if rows else None

    def update_entry(self, user_id: str, entry_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        if self._client is None:
            entry = self.get_entry(user_id=user_id, entry_id=entry_id)
            if entry is None:
                return None
            entry.update(payload)
            return entry

        try:
            result = (
                self._client.table(self._settings.supabase_journals_table)
                .update(payload)
                .eq("user_id", user_id)
                .eq("id", entry_id)
                .execute()
            )
        except APIError as exc:
            raise JournalRepositoryError(f"Supabase journal update failed: {exc.message}") from exc

        rows = result.data or []
        return rows[0] if rows else None

    @staticmethod
    def _filter_local_entries(
        rows: list[dict[str, Any]],
        *,
        month: str | None,
        query: str | None,
        tag: str | None,
    ) -> list[dict[str, Any]]:
        filtered = rows
        if month:
            filtered = [entry for entry in filtered if str(entry.get("created_at", "")).startswith(month)]
        if query:
            needle = query.lower()
            filtered = [
                entry
                for entry in filtered
                if needle in str(entry.get("title", "")).lower()
                or needle in str(entry.get("summary", "")).lower()
                or needle in str(entry.get("transcript", "")).lower()
            ]
        if tag:
            filtered = [entry for entry in filtered if tag in (entry.get("themes") or [])]
        return filtered

    @staticmethod
    def _next_month(month: str) -> str:
        year_raw, month_raw = month.split("-", maxsplit=1)
        year = int(year_raw)
        month_number = int(month_raw)
        if month_number == 12:
            return f"{year + 1}-01-01"
        return f"{year}-{month_number + 1:02d}-01"
