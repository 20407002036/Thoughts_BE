from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Mindful Moments Backend"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    auth_required: bool = True

    supabase_url: Optional[str] = None
    supabase_anon_key: Optional[str] = None
    supabase_service_role_key: Optional[str] = None
    supabase_jwt_secret: Optional[str] = None
    supabase_jwt_audience: str = "authenticated"
    supabase_bucket: str = "journal-audio"
    supabase_journals_table: str = "journals"
    supabase_profiles_table: str = "user_profiles"
    signed_url_expiry_seconds: int = 3600

    groq_api_key: Optional[str] = None
    groq_whisper_model: str = "whisper-large-v3"
    groq_llm_model: str = "llama-3.3-70b-versatile"

    vosk_model_path: Optional[str] = None

    max_upload_mb: int = 12
    request_timeout_seconds: int = 30
    analysis_prompt_version: str = "v1"
    cors_allow_origins: str = "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173"
    cors_allow_credentials: bool = True
    cors_allow_methods: str = "*"
    cors_allow_headers: str = "*"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/0"
    ingest_async: bool = False
    rate_limit_enabled: bool = True
    rate_limit_ingest_per_minute: int = 5
    rate_limit_ingest_per_hour: int = 30
    redis_url: str = "redis://localhost:6379/0"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False)

    @property
    def supabase_jwks_url(self) -> Optional[str]:
        if not self.supabase_url:
            return None
        return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"

    @property
    def supabase_jwt_issuer(self) -> Optional[str]:
        if not self.supabase_url:
            return None
        return f"{self.supabase_url.rstrip('/')}/auth/v1"

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]

    @property
    def cors_methods(self) -> list[str]:
        return [method.strip() for method in self.cors_allow_methods.split(",") if method.strip()]

    @property
    def cors_headers(self) -> list[str]:
        return [header.strip() for header in self.cors_allow_headers.split(",") if header.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
