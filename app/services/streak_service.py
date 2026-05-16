from datetime import datetime, timezone, timedelta
import logging
import zoneinfo

from app.repositories.profile_repository import ProfileRepository

logger = logging.getLogger(__name__)

class StreakService:
    def __init__(self, profile_repository: ProfileRepository) -> None:
        self._profile_repository = profile_repository

    def update_streak(self, user_id: str) -> None:
        """
        Updates the user's streak based on the current time and their last journal entry.
        Logic uses the user's local timezone to determine calendar days.
        """
        profile = self._profile_repository.get_profile(user_id)
        user_tz_str = profile.get("timezone", "UTC")

        try:
            user_tz = zoneinfo.ZoneInfo(user_tz_str)
        except zoneinfo.ZoneInfoNotFoundError:
            logger.warning(f"Invalid timezone {user_tz_str} for user {user_id}, defaulting to UTC")
            user_tz = zoneinfo.ZoneInfo("UTC")

        now_utc = datetime.now(timezone.utc)
        now_local = now_utc.astimezone(user_tz)
        today_local = now_local.date()

        last_saved_str = profile.get("last_journal_saved")
        current_streak = profile.get("streak_count", 0)

        if not last_saved_str:
            # First entry ever
            new_streak = 1
        else:
            try:
                last_saved_utc = datetime.fromisoformat(last_saved_str.replace("Z", "+00:00"))
                last_saved_local = last_saved_utc.astimezone(user_tz)
                last_date_local = last_saved_local.date()
            except ValueError:
                logger.error(f"Malformed last_journal_saved timestamp for user {user_id}: {last_saved_str}")
                new_streak = 1
                last_date_local = None

            if last_date_local == today_local:
                # Already journaled today
                new_streak = current_streak
            elif last_date_local == today_local - timedelta(days=1):
                # Journaled yesterday, streak continues
                new_streak = current_streak + 1
            else:
                # Missed a day or more
                new_streak = 1

        self._profile_repository.update_streak(
            user_id=user_id,
            streak_count=new_streak,
            last_journal_saved=now_utc.isoformat()
        )

    def reset_expired_streaks(self) -> int:
        """
        Resets streaks for users who have been inactive for more than 48 hours.
        Returns the number of resets performed.
        """
        # 48 hours ago from now
        cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
        cutoff_str = cutoff.isoformat()

        expired_profiles = self._profile_repository.get_profiles_inactive_since(cutoff_str)

        reset_count = 0
        for profile in expired_profiles:
            user_id = profile["id"]
            self._profile_repository.reset_streak(user_id)
            reset_count += 1

        return reset_count
