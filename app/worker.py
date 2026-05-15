import logging
from celery import Celery
from app.core.settings import get_settings
from app.repositories.profile_repository import ProfileRepository
from app.services.streak_service import StreakService

settings = get_settings()
logger = logging.getLogger(__name__)

# Initialize Celery
# We expect CELERY_BROKER_URL and CELERY_RESULT_BACKEND to be in the environment or settings
app = Celery(
    "mindful_moments_worker",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)

# Setup beat schedule for the "Reaper" task
app.conf.beat_schedule = {
    "reset-expired-streaks-hourly": {
        "task": "worker.reset_streaks",
        "schedule": 3600.0,  # Every hour
    },
}

@app.task(name="worker.reset_streaks")
def reset_streaks():
    """
    Celery task to find users who haven't journaled in 48 hours
    and reset their streak to 0.
    """
    logger.info("Running scheduled streak reset task...")
    try:
        # Manually instantiate dependencies for the worker
        profile_repo = ProfileRepository(settings)
        streak_service = StreakService(profile_repo)

        reset_count = streak_service.reset_expired_streaks()
        logger.info(f"Streak reset task completed. Reset {reset_count} streaks.")
    except Exception as exc:
        logger.exception("Failed to reset expired streaks")
        return False
    return True
