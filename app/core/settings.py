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

    # Base64-encoded 32-byte AES-256 key for at-rest encryption of journal
    # contents. None disables encryption (plaintext rows, local dev mode).
    journal_encryption_key: Optional[str] = None

    groq_api_key: Optional[str] = None
    groq_whisper_model: str = "whisper-large-v3"
    groq_llm_model: str = "llama-3.3-70b-versatile"

    vosk_model_path: Optional[str] = None

    max_upload_mb: int = 12
    request_timeout_seconds: int = 30
    analysis_prompt_version: str = "v1"
    cors_allow_origins: str = "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173"
    cors_allow_credentials: bool = True
    cors_allow_methods: str = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
    cors_allow_headers: str = "Authorization,Content-Type,Accept,Origin"
    # Firebase Cloud Messaging. Disabled by default so local development and
    # CI keep working without a service account; PushService no-ops while
    # firebase_enabled is false.
    firebase_enabled: bool = False
    firebase_project_id: Optional[str] = None
    # Path to a service account JSON file, or the raw JSON inline (useful for
    # Render env vars, where there is no filesystem to point at).
    firebase_credentials_path: Optional[str] = None
    firebase_credentials_json: Optional[str] = None
    # When true, messages are validated and logged but never delivered. Keep
    # true in staging so production devices are not disturbed.
    firebase_dry_run: bool = False
    firebase_devices_table: str = "device_tokens"
    firebase_notification_log_table: str = "notification_log"
    # FCM caps a single multicast at 500 tokens; send_each_for_multicast does
    # not chunk for us, so we do it here.
    fcm_batch_size: int = 500

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

    @property
    def firebase_configured(self) -> bool:
        """True when FCM is enabled and enough config is present to attempt a send.

        Treats a bare FIREBASE_PROJECT_ID as configured even without an
        explicit credential, because that is the shape used on platforms where
        the service account is attached to the runtime and the Admin SDK
        resolves it through Application Default Credentials. A false negative
        here only affects the /ready report, never delivery.
        """
        return bool(
            self.firebase_enabled
            and (self.firebase_credentials_path or self.firebase_credentials_json or self.firebase_project_id)
        )

    @property
    def firebase_credentials(self) -> Optional[dict]:
        """Return the service account as a dict, or None for ADC resolution.

        The Admin SDK already falls back to Application Default Credentials
        (and the GCE metadata server) when handed no argument, so a project
        deployed on a platform with attached credentials needs no config at
        all. `firebase_credentials` returning None means "let the SDK decide".
        """
        if self.firebase_credentials_json:
            import json

            try:
                parsed = json.loads(self.firebase_credentials_json)
            except ValueError as exc:
                raise ValueError("FIREBASE_CREDENTIALS_JSON is not valid JSON") from exc
            if not isinstance(parsed, dict):
                raise ValueError("FIREBASE_CREDENTIALS_JSON must be a JSON object")
            return parsed
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
