from datetime import datetime, timezone
from typing import Any

from app.models.schemas import (
    JournalEntryDetail,
    JournalEntryListResponse,
    JournalEntrySummary,
    JournalTag,
    MoodAnalysis,
    Transcript,
)
from app.repositories.journal_repository import JournalRepository


class JournalNotFoundError(RuntimeError):
    """Raised when a journal entry does not exist for the current user."""


class JournalValidationError(RuntimeError):
    """Raised when journal update input is invalid."""


class JournalService:
    def __init__(self, journal_repository: JournalRepository) -> None:
        self._journal_repository = journal_repository

    def list_entries(
        self,
        user_id: str,
        *,
        limit: int,
        offset: int,
        month: str | None,
        query: str | None,
        tag: str | None,
    ) -> JournalEntryListResponse:
        rows, total = self._journal_repository.list_entries(
            user_id=user_id,
            limit=limit,
            offset=offset,
            month=month,
            query=query,
            tag=tag,
        )
        return JournalEntryListResponse(
            entries=[self._summary_from_row(row) for row in rows],
            limit=limit,
            offset=offset,
            total=total,
        )

    def get_entry(self, user_id: str, entry_id: str) -> JournalEntryDetail:
        row = self._journal_repository.get_entry(user_id=user_id, entry_id=entry_id)
        if row is None:
            raise JournalNotFoundError("Journal entry not found")
        return self._detail_from_row(row)

    def update_entry(
        self,
        user_id: str,
        entry_id: str,
        *,
        title: str | None,
        summary: str | None,
        tags: list[str] | None,
    ) -> JournalEntryDetail:
        payload: dict[str, Any] = {}
        if title is not None:
            normalized_title = title.strip()
            if not normalized_title:
                raise JournalValidationError("Title cannot be blank")
            payload["title"] = normalized_title
        if summary is not None:
            payload["summary"] = summary.strip()
        if tags is not None:
            payload["themes"] = [tag.strip() for tag in tags if tag.strip()]

        if not payload:
            return self.get_entry(user_id=user_id, entry_id=entry_id)

        row = self._journal_repository.update_entry(user_id=user_id, entry_id=entry_id, payload=payload)
        if row is None:
            raise JournalNotFoundError("Journal entry not found")
        return self._detail_from_row(row)

    @classmethod
    def _summary_from_row(cls, row: dict[str, Any]) -> JournalEntrySummary:
        created_at = cls._parse_datetime(row.get("created_at"))
        return JournalEntrySummary(
            id=str(row.get("id", "")),
            entry_id=str(row.get("id", "")),
            title=str(row.get("title") or "Untitled"),
            created_at=created_at,
            summary=str(row.get("summary") or ""),
            mood_label=row.get("mood"),
        )

    @classmethod
    def _detail_from_row(cls, row: dict[str, Any]) -> JournalEntryDetail:
        created_at = cls._parse_datetime(row.get("created_at"))
        insights = row.get("insights") or []
        themes = row.get("themes") or []
        return JournalEntryDetail(
            id=str(row.get("id", "")),
            recording_session_id=row.get("recording_session_id"),
            title=str(row.get("title") or "Untitled"),
            created_at=created_at,
            recorded_at=cls._parse_datetime(row.get("recorded_at")) if row.get("recorded_at") else created_at,
            transcript=Transcript(full_text=str(row.get("transcript") or "")),
            tags=[JournalTag(label=str(theme)) for theme in themes],
            mood_analysis=MoodAnalysis(
                label=str(row.get("mood") or "unknown"),
                explanation=row.get("mood_explanation"),
            ),
            takeaway=row.get("takeaway") or row.get("summary"),
            summary=row.get("summary"),
            highlights=[str(insight) for insight in insights],
            audio_path=row.get("audio_path"),
            audio_signed_url=row.get("audio_signed_url"),
            prompt_version=row.get("prompt_version"),
        )

    @staticmethod
    def _parse_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        if value:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return datetime.now(timezone.utc)
