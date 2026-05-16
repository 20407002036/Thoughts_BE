from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import pytest
import zoneinfo

from app.services.streak_service import StreakService
from app.repositories.profile_repository import ProfileRepository

@pytest.fixture
def mock_repo():
    return MagicMock(spec=ProfileRepository)

@pytest.fixture
def streak_service(mock_repo):
    return StreakService(mock_repo)

def test_update_streak_first_entry(streak_service, mock_repo):
    user_id = "user-1"
    mock_repo.get_profile.return_value = {
        "timezone": "UTC",
        "streak_count": 0,
        "last_journal_saved": None
    }

    with patch("app.services.streak_service.datetime") as mock_datetime:
        now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
        mock_datetime.now.return_value = now
        mock_datetime.fromisoformat = datetime.fromisoformat

        streak_service.update_streak(user_id)

        mock_repo.update_streak.assert_called_once_with(
            user_id=user_id,
            streak_count=1,
            last_journal_saved=now.isoformat()
        )

def test_update_streak_same_day(streak_service, mock_repo):
    user_id = "user-1"
    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)

    mock_repo.get_profile.return_value = {
        "timezone": "UTC",
        "streak_count": 5,
        "last_journal_saved": now.isoformat()
    }

    with patch("app.services.streak_service.datetime") as mock_datetime:
        mock_datetime.now.return_value = now
        mock_datetime.fromisoformat = datetime.fromisoformat

        streak_service.update_streak(user_id)

        mock_repo.update_streak.assert_called_once_with(
            user_id=user_id,
            streak_count=5,
            last_journal_saved=now.isoformat()
        )

def test_update_streak_consecutive_day(streak_service, mock_repo):
    user_id = "user-1"
    yesterday = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)
    today = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)

    mock_repo.get_profile.return_value = {
        "timezone": "UTC",
        "streak_count": 5,
        "last_journal_saved": yesterday.isoformat()
    }

    with patch("app.services.streak_service.datetime") as mock_datetime:
        mock_datetime.now.return_value = today
        mock_datetime.fromisoformat = datetime.fromisoformat

        streak_service.update_streak(user_id)

        mock_repo.update_streak.assert_called_once_with(
            user_id=user_id,
            streak_count=6,
            last_journal_saved=today.isoformat()
        )

def test_update_streak_gap_day(streak_service, mock_repo):
    user_id = "user-1"
    long_ago = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc)
    today = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)

    mock_repo.get_profile.return_value = {
        "timezone": "UTC",
        "streak_count": 5,
        "last_journal_saved": long_ago.isoformat()
    }

    with patch("app.services.streak_service.datetime") as mock_datetime:
        mock_datetime.now.return_value = today
        mock_datetime.fromisoformat = datetime.fromisoformat

        streak_service.update_streak(user_id)

        mock_repo.update_streak.assert_called_once_with(
            user_id=user_id,
            streak_count=1,
            last_journal_saved=today.isoformat()
        )

def test_update_streak_timezone_handling(streak_service, mock_repo):
    # User in Tokyo (UTC+9)
    # Today is 2026-05-16 UTC.
    # In Tokyo, it's already 2026-05-16.
    user_id = "user-tokyo"

    # Mock now to be 2026-05-16 01:00 UTC
    now_utc = datetime(2026, 5, 16, 1, 0, 0, tzinfo=timezone.utc)
    # Tokyo time: 2026-05-16 10:00

    # Last entry was 2026-05-15 23:00 UTC
    # Tokyo time: 2026-05-16 08:00 (Same day!)
    last_saved_utc = datetime(2026, 5, 15, 23, 0, 0, tzinfo=timezone.utc)

    mock_repo.get_profile.return_value = {
        "timezone": "Asia/Tokyo",
        "streak_count": 2,
        "last_journal_saved": last_saved_utc.isoformat()
    }

    with patch("app.services.streak_service.datetime") as mock_datetime:
        mock_datetime.now.return_value = now_utc
        mock_datetime.fromisoformat = datetime.fromisoformat

        streak_service.update_streak(user_id)

        # Should be same day in Tokyo, streak remains 2
        mock_repo.update_streak.assert_called_once_with(
            user_id=user_id,
            streak_count=2,
            last_journal_saved=now_utc.isoformat()
        )

def test_update_streak_invalid_timezone(streak_service, mock_repo):
    user_id = "user-1"
    mock_repo.get_profile.return_value = {
        "timezone": "Invalid/Tz",
        "streak_count": 0,
        "last_journal_saved": None
    }

    with patch("app.services.streak_service.datetime") as mock_datetime:
        now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
        mock_datetime.now.return_value = now
        mock_datetime.fromisoformat = datetime.fromisoformat

        streak_service.update_streak(user_id)

        mock_repo.update_streak.assert_called_once_with(
            user_id=user_id,
            streak_count=1,
            last_journal_saved=now.isoformat()
        )

def test_update_streak_malformed_timestamp(streak_service, mock_repo):
    user_id = "user-1"
    mock_repo.get_profile.return_value = {
        "timezone": "UTC",
        "streak_count": 5,
        "last_journal_saved": "not-a-date"
    }

    with patch("app.services.streak_service.datetime") as mock_datetime:
        now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=timezone.utc)
        mock_datetime.now.return_value = now
        # We don't override fromisoformat here, it will be called by the service
        # and we want it to raise ValueError since "not-a-date" is passed.
        # But the service uses datetime.fromisoformat.
        # To make it raise, we can't just mock the class, we need the real method to fail.
        # Actually, if we patch datetime, we can control fromisoformat.
        mock_datetime.fromisoformat.side_effect = ValueError("Invalid format")

        streak_service.update_streak(user_id)

        mock_repo.update_streak.assert_called_once_with(
            user_id=user_id,
            streak_count=1,
            last_journal_saved=now.isoformat()
        )

def test_reset_expired_streaks_success(streak_service, mock_repo):
    mock_repo.get_profiles_inactive_since.return_value = [
        {"id": "user-1", "streak_count": 5},
        {"id": "user-2", "streak_count": 10}
    ]

    count = streak_service.reset_expired_streaks()

    assert count == 2
    assert mock_repo.reset_streak.call_count == 2
    mock_repo.reset_streak.assert_any_call("user-1")
    mock_repo.reset_streak.assert_any_call("user-2")

def test_reset_expired_streaks_none(streak_service, mock_repo):
    mock_repo.get_profiles_inactive_since.return_value = []

    count = streak_service.reset_expired_streaks()

    assert count == 0
    mock_repo.reset_streak.assert_not_called()
