from app.core.settings import Settings
from app.repositories.journal_repository import JournalRepository
from app.repositories.profile_repository import ProfileRepository
from app.services.journal_service import JournalNotFoundError, JournalService
from app.services.profile_service import ProfileService


def _journal_repository_with_entries() -> JournalRepository:
    repository = JournalRepository(Settings(supabase_url=None, supabase_service_role_key=None))
    repository.create_entry(
        {
            "user_id": "user-1",
            "transcript": "Today I felt calm and focused.",
            "mood": "calm",
            "title": "Quiet morning",
            "summary": "A reflective morning entry.",
            "themes": ["gratitude", "focus"],
            "insights": ["A slower start helped."],
            "audio_path": "user-1/entry.mp3",
            "audio_signed_url": None,
            "prompt_version": "v1",
            "created_at": "2026-05-04T08:00:00Z",
        }
    )
    repository.create_entry(
        {
            "user_id": "user-1",
            "transcript": "A second note about planning.",
            "mood": "energized",
            "title": "Planning",
            "summary": "A planning entry.",
            "themes": ["planning"],
            "insights": ["Write the next step down."],
            "audio_path": "user-1/planning.mp3",
            "audio_signed_url": None,
            "prompt_version": "v1",
            "created_at": "2026-04-30T08:00:00Z",
        }
    )
    return repository


def test_journal_service_lists_searches_and_details_real_entries() -> None:
    service = JournalService(_journal_repository_with_entries())

    page = service.list_entries(user_id="user-1", limit=10, offset=0, month="2026-05", query="calm", tag="gratitude")

    assert page.total == 1
    assert page.entries[0].title == "Quiet morning"
    assert page.entries[0].mood_label == "calm"

    detail = service.get_entry(user_id="user-1", entry_id=page.entries[0].id)
    assert detail.transcript.full_text == "Today I felt calm and focused."
    assert detail.mood_analysis.label == "calm"
    assert [tag.label for tag in detail.tags] == ["gratitude", "focus"]
    assert detail.highlights == ["A slower start helped."]


def test_journal_service_updates_title_summary_and_tags() -> None:
    service = JournalService(_journal_repository_with_entries())
    entry = service.list_entries(user_id="user-1", limit=1, offset=0, month=None, query=None, tag=None).entries[0]

    updated = service.update_entry(
        user_id="user-1",
        entry_id=entry.id,
        title="Updated title",
        summary="Updated summary",
        tags=["reviewed", "calm"],
    )

    assert updated.title == "Updated title"
    assert updated.summary == "Updated summary"
    assert [tag.label for tag in updated.tags] == ["reviewed", "calm"]


def test_journal_service_get_entry_refreshes_audio_signed_url_with_storage_service() -> None:
    class _FakeStorageService:
        def __init__(self) -> None:
            self.called_with: str | None = None

        def signed_url_for_path(self, storage_path: str) -> str:
            self.called_with = storage_path
            return "https://signed.example.com/audio"

    storage = _FakeStorageService()
    service = JournalService(_journal_repository_with_entries(), storage_service=storage)
    page = service.list_entries(user_id="user-1", limit=1, offset=0, month=None, query=None, tag=None)
    entry_id = page.entries[0].id

    detail = service.get_entry(user_id="user-1", entry_id=entry_id)

    assert storage.called_with == detail.audio_path
    assert detail.audio_signed_url == "https://signed.example.com/audio"


def test_journal_service_raises_not_found_for_missing_entry() -> None:
    service = JournalService(_journal_repository_with_entries())

    try:
        service.get_entry(user_id="user-1", entry_id="missing")
    except JournalNotFoundError as exc:
        assert str(exc) == "Journal entry not found"
    else:
        raise AssertionError("Expected JournalNotFoundError")


def test_profile_preferences_are_stored_without_placeholders() -> None:
    repository = ProfileRepository(Settings(supabase_url=None, supabase_service_role_key=None))
    profile_service = ProfileService(repository)

    profile = profile_service.update_display_name(user_id="user-1", email="marcus@example.com", display_name="Marcus Lee")
    preferences = repository.update_preferences(
        user_id="user-1",
        payload={
            "notifications_enabled": False,
            "prompt_reminder_time": "08:30",
            "appearance_mode": "dark",
            "audio_quality": "high",
            "language": "en",
        },
    )

    assert profile["display_name"] == "Marcus Lee"
    assert profile["initials"] == "ML"
    assert preferences["notifications_enabled"] is False
    assert preferences["prompt_reminder_time"] == "08:30"
    assert preferences["appearance_mode"] == "dark"
