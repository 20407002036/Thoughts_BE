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

    def signed_url_for_path(self, storage_path: str) -> str | None:
        """Return a fresh signed URL for an existing storage path or None in local-fallback mode."""
        if self._client is None:
            return None

        signed = self._client.storage.from_(self._settings.supabase_bucket).create_signed_url(
            storage_path,
            self._settings.signed_url_expiry_seconds,
        )
        return signed.get("signedURL") if isinstance(signed, dict) else None

    def download_audio(self, storage_path: str) -> bytes:
        """Fetch previously uploaded audio bytes by storage path.

        Used by the async ingest worker to read files that the API process uploaded
        synchronously. In local-fallback mode this reads from the local uploads tree.
        """
        if self._client is None:
            local_path = self._local_upload_dir / storage_path
            if not local_path.exists():
                raise FileNotFoundError(f"Audio not found at {local_path}")
            return local_path.read_bytes()

        return self._client.storage.from_(self._settings.supabase_bucket).download(storage_path)

    def delete_audio(self, storage_path: str) -> None:
        """Remove an audio blob from storage.

        Called by the pipeline to clean up orphaned uploads when a later stage
        (transcription, analysis, persist) fails after the audio was already stored.
        """
        if self._client is None:
            local_path = self._local_upload_dir / storage_path
            local_path.unlink(missing_ok=True)
            return

        self._client.storage.from_(self._settings.supabase_bucket).remove([storage_path])
