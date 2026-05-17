from datetime import datetime, timezone
import pytest
from unittest.mock import MagicMock
from app.repositories.profile_repository import ProfileRepository
from app.core.settings import Settings

@pytest.fixture
def mock_settings():
    return Settings(
        supabase_url=None,
        supabase_service_role_key=None,
        supabase_profiles_table="profiles"
    )

@pytest.fixture
def repo(mock_settings):
    return ProfileRepository(mock_settings)

def test_update_streak_with_timestamp(repo):
    user_id = "user-1"
    timestamp = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc).isoformat()

    repo.update_streak(user_id, 5, timestamp)
    profile = repo.get_profile(user_id)

    assert profile["streak_count"] == 5
    assert profile["last_journal_saved"] == timestamp

def test_update_streak_without_timestamp(repo):
    user_id = "user-1"
    initial_timestamp = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc).isoformat()

    # Set initial state
    repo.update_streak(user_id, 1, initial_timestamp)

    # Update streak count only
    repo.update_streak(user_id, 5)
    profile = repo.get_profile(user_id)

    assert profile["streak_count"] == 5
    assert profile["last_journal_saved"] == initial_timestamp

def test_reset_streak_preserves_timestamp(repo):
    user_id = "user-1"
    initial_timestamp = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc).isoformat()

    # Set initial state
    repo.update_streak(user_id, 5, initial_timestamp)

    # Reset streak
    repo.reset_streak(user_id)
    profile = repo.get_profile(user_id)

    assert profile["streak_count"] == 0
    assert profile["last_journal_saved"] == initial_timestamp
