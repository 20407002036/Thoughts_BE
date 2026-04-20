from fastapi import APIRouter

from app.core.settings import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready() -> dict[str, str | bool]:
    settings = get_settings()
    return {
        "status": "ready",
        "environment": settings.app_env,
        "auth_required": settings.auth_required,
        "supabase_configured": bool(settings.supabase_url and settings.supabase_service_role_key),
        "groq_configured": bool(settings.groq_api_key),
    }
