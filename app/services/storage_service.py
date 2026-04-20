from pathlib import Path
from uuid import uuid4

from supabase import Client, create_client

from app.core.settings import Settings


class StorageService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Client | None = None
        self._local_upload_dir = Path("uploads")
        self._local_upload_dir.mkdir(parents=True, exist_ok=True)

        if settings.supabase_url and settings.supabase_service_role_key:
            self._client = create_client(settings.supabase_url, settings.supabase_service_role_key)

    def upload_audio(self, user_id: str, filename: str | None, content: bytes, content_type: str) -> tuple[str, str | None]:
        extension = Path(filename).suffix if filename else ".mp3"
        if not extension:
            extension = ".mp3"

        storage_path = f"{user_id}/{uuid4()}{extension}"

        if self._client is None:
            local_path = self._local_upload_dir / storage_path
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(content)
            return storage_path, None

        self._client.storage.from_(self._settings.supabase_bucket).upload(
            storage_path,
            content,
            file_options={"content-type": content_type, "upsert": "false"},
        )

        signed = self._client.storage.from_(self._settings.supabase_bucket).create_signed_url(
            storage_path,
            self._settings.signed_url_expiry_seconds,
        )
        signed_url = signed.get("signedURL") if isinstance(signed, dict) else None
        return storage_path, signed_url
