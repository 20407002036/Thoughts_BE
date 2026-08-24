from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from postgrest.exceptions import APIError
from supabase import Client, create_client

from app.core.crypto import JournalCipher, JournalCryptoError
from app.core.settings import Settings


class JournalRepositoryError(RuntimeError):
    """Raised when journal persistence fails."""


# Fields sealed inside data_encrypted. Everything else stays a real column so
# SQL can filter/order by it (user_id, created_at, audio_path, ...).
SENSITIVE_FIELDS = (
    "transcript",
    "title",
    "summary",
    "takeaway",
    "mood",
    "mood_explanation",
    "themes",
    "insights",
)

# Upper bound on rows pulled client-side when search/tag filtering must run
# in Python because the columns are encrypted and unsearchable in SQL.
SEARCH_FETCH_LIMIT = 1000


class JournalRepository:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cipher = JournalCipher(settings)
        self._client: Client | None = None
        self._local_entries: list[dict[str, Any]] = []

        if settings.supabase_url and settings.supabase_service_role_key:
            self._client = create_client(settings.supabase_url, settings.supabase_service_role_key)

    def create_entry(self, payload: dict[str, Any]) -> dict[str, Any]:
        user_id = str(payload.get("user_id", ""))
        row = self._seal(payload, user_id)

        if self._client is None:
            entry = {
                **row,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._local_entries.append(entry)
            return self._unseal(entry, user_id)

        try:
            result = self._client.table(self._settings.supabase_journals_table).insert(row).execute()
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
        return self._unseal(data[0], user_id)

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
        # Encrypted columns cannot be filtered in SQL, so query/tag searches
        # decrypt rows client-side and reuse the same matching logic as the
        # local fallback.
        needs_python_filter = bool(query or tag)

        if self._client is None:
            rows = [entry for entry in self._local_entries if entry.get("user_id") == user_id]
            rows = [self._unseal(row, user_id) for row in rows]
            rows = self._filter_decrypted_rows(rows, month=month, query=query, tag=tag)
            rows.sort(key=lambda entry: str(entry.get("created_at", "")), reverse=True)
            return rows[offset : offset + limit], len(rows)

        if needs_python_filter:
            try:
                request = (
                    self._client.table(self._settings.supabase_journals_table)
                    .select("*")
                    .eq("user_id", user_id)
                    .order("created_at", desc=True)
                    .limit(SEARCH_FETCH_LIMIT)
                )
                if month:
                    request = request.gte("created_at", f"{month}-01").lt("created_at", self._next_month(month))
                result = request.execute()
            except APIError as exc:
                raise JournalRepositoryError(f"Supabase journal list failed: {exc.message}") from exc

            rows = [self._unseal(row, user_id) for row in result.data or []]
            rows = self._filter_decrypted_rows(rows, month=None, query=query, tag=tag)
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

            result = request.execute()
        except APIError as exc:
            raise JournalRepositoryError(f"Supabase journal list failed: {exc.message}") from exc

        rows = [self._unseal(row, user_id) for row in result.data or []]
        return rows, result.count or 0

    def get_entry(self, user_id: str, entry_id: str) -> dict[str, Any] | None:
        if self._client is None:
            entry = next(
                (
                    entry
                    for entry in self._local_entries
                    if entry.get("user_id") == user_id and str(entry.get("id")) == entry_id
                ),
                None,
            )
            return self._unseal(entry, user_id) if entry else None

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
        return self._unseal(rows[0], user_id) if rows else None

    def update_entry(self, user_id: str, entry_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        current = self.get_entry(user_id=user_id, entry_id=entry_id)
        if current is None:
            return None

        if self._cipher.enabled:
            merged = {**current, **payload}
            row = self._seal(merged, user_id)
            db_payload: dict[str, Any] = {
                "data_encrypted": row["data_encrypted"],
                "enc_version": 1,
            }
        else:
            db_payload = {key: value for key, value in payload.items() if key in SENSITIVE_FIELDS}

        if self._client is None:
            stored = next(
                (
                    entry
                    for entry in self._local_entries
                    if entry.get("user_id") == user_id and str(entry.get("id")) == entry_id
                ),
                None,
            )
            if stored is None:
                return None
            if self._cipher.enabled:
                stored["data_encrypted"] = db_payload["data_encrypted"]
                stored["enc_version"] = 1
            else:
                stored.update(db_payload)
            return self._unseal(stored, user_id)

        try:
            result = (
                self._client.table(self._settings.supabase_journals_table)
                .update(db_payload)
                .eq("user_id", user_id)
                .eq("id", entry_id)
                .execute()
            )
        except APIError as exc:
            raise JournalRepositoryError(f"Supabase journal update failed: {exc.message}") from exc

        rows = result.data or []
        return self._unseal(rows[0], user_id) if rows else None

    def _seal(self, payload: dict[str, Any], user_id: str) -> dict[str, Any]:
        """Split a plaintext payload into DB columns plus an encrypted blob."""
        row = {key: value for key, value in payload.items() if key not in SENSITIVE_FIELDS}
        if self._cipher.enabled:
            sensitive = {key: payload[key] for key in SENSITIVE_FIELDS if key in payload}
            row["data_encrypted"] = self._cipher.encrypt_fields(sensitive, user_id)
            row["enc_version"] = 1
        else:
            row.update({key: payload[key] for key in SENSITIVE_FIELDS if key in payload})
        return row

    def _unseal(self, row: dict[str, Any], user_id: str) -> dict[str, Any]:
        """Return a plaintext dict regardless of whether the row is encrypted."""
        blob = row.get("data_encrypted")
        if blob:
            try:
                sensitive = self._cipher.decrypt_blob(blob, user_id)
            except JournalCryptoError as exc:
                raise JournalRepositoryError(str(exc)) from exc
            return {key: value for key, value in row.items() if key != "data_encrypted"} | sensitive
        return dict(row)

    @staticmethod
    def _filter_decrypted_rows(
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
