from pathlib import Path

import pytest
from postgrest.exceptions import APIError

from app.core.settings import Settings
from app.repositories.journal_repository import JournalRepository, JournalRepositoryError
from app.services.storage_service import StorageService


def test_storage_service_writes_to_local_uploads_when_supabase_not_configured(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    service = StorageService(Settings(supabase_url=None, supabase_service_role_key=None))

    storage_path, signed_url = service.upload_audio(
        user_id="user-1",
        filename="entry.mp3",
        content=b"audio-bytes",
        content_type="audio/mpeg",
    )

    assert signed_url is None
    assert storage_path.startswith("user-1/")
    assert storage_path.endswith(".mp3")
    assert (tmp_path / "uploads" / storage_path).exists()


def test_journal_repository_creates_local_entry_when_supabase_not_configured() -> None:
    repository = JournalRepository(Settings(supabase_url=None, supabase_service_role_key=None))

    saved = repository.create_entry(
        {
            "id": "test-entry-1",
            "user_id": "user-1",
            "transcript": "Today was calm",
            "mood": "calm",
            "title": "Quiet morning",
            "summary": "Reflective notes",
            "themes": ["gratitude"],
            "insights": ["slow down"],
            "audio_path": "user-1/entry.mp3",
            "audio_signed_url": None,
            "prompt_version": "v1",
        }
    )

    assert saved["id"]
    assert saved["created_at"]
    assert saved["user_id"] == "user-1"
    assert saved["title"] == "Quiet morning"


def test_journal_repository_surfaces_supabase_insert_api_errors() -> None:
    class _FakeInsert:
        def execute(self):
            raise APIError(
                {
                    "message": "invalid input syntax for type uuid",
                    "code": "22P02",
                    "details": "Token \"dev-local-user\" is invalid.",
                    "hint": None,
                }
            )

    class _FakeTable:
        def insert(self, payload):
            _ = payload
            return _FakeInsert()

    class _FakeClient:
        def table(self, _table_name: str):
            return _FakeTable()

    repository = JournalRepository(Settings(supabase_url=None, supabase_service_role_key=None))
    repository._client = _FakeClient()  # type: ignore[assignment]

    with pytest.raises(JournalRepositoryError, match="Supabase insert failed") as exc_info:
        repository.create_entry(
            {
                "user_id": "dev-local-user",
                "transcript": "Today was calm",
                "mood": "calm",
                "title": "Quiet morning",
                "summary": "Reflective notes",
                "themes": ["gratitude"],
                "insights": ["slow down"],
                "audio_path": "user-1/entry.mp3",
                "audio_signed_url": None,
                "prompt_version": "v1",
            }
        )

    assert "code=22P02" in str(exc_info.value)
