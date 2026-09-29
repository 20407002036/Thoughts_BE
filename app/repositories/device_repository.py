import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from postgrest.exceptions import APIError
from supabase import Client, create_client

from app.core.settings import Settings

logger = logging.getLogger(__name__)


class DeviceRepositoryError(RuntimeError):
    """Raised when device token persistence fails."""


class DeviceRepository:
    """Stores the FCM registration tokens owned by each user.

    FCM hands out a token per app installation and rotates it on reinstall and
    on restore-from-backup, so `register_device` is an upsert keyed on the
    token rather than on (user_id, token). Re-registering a token that already
    belongs to someone else reassigns it — the new account is the live one, and
    leaving the old row active would leak that device's push to the previous
    user.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Client | None = None
        self._local_devices: dict[str, dict[str, Any]] = {}
        self._local_notification_log: list[dict[str, Any]] = []

        if settings.supabase_url and settings.supabase_service_role_key:
            self._client = create_client(settings.supabase_url, settings.supabase_service_role_key)

    def _table(self) -> str:
        return self._settings.firebase_devices_table

    def _log_table(self) -> str:
        return self._settings.firebase_notification_log_table

    def register_device(
        self,
        user_id: str,
        token: str,
        *,
        platform: str = "android",
        app_version: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        row = {
            "id": str(uuid4()),
            "user_id": user_id,
            "token": token,
            "platform": platform,
            "app_version": app_version,
            "active": True,
            "created_at": now,
            "last_seen_at": now,
        }

        if self._client is None:
            existing = self._local_devices.get(token)
            if existing is not None:
                row["id"] = existing["id"]
                row["created_at"] = existing["created_at"]
            self._local_devices[token] = row
            return dict(row)

        try:
            result = (
                self._client.table(self._table())
                .upsert(row, on_conflict="token")
                .execute()
            )
        except APIError as exc:
            raise DeviceRepositoryError(f"Supabase device upsert failed: {exc.message}") from exc

        rows = result.data or []
        if not rows:
            raise DeviceRepositoryError("Supabase device upsert returned no row")
        return rows[0]

    def list_active_tokens(self, user_id: str) -> list[str]:
        if self._client is None:
            return [
                device["token"]
                for device in self._local_devices.values()
                if device.get("user_id") == user_id and device.get("active")
            ]

        try:
            result = (
                self._client.table(self._table())
                .select("token")
                .eq("user_id", user_id)
                .eq("active", True)
                .execute()
            )
        except APIError as exc:
            raise DeviceRepositoryError(f"Supabase device list failed: {exc.message}") from exc

        return [row["token"] for row in (result.data or []) if row.get("token")]

    def deactivate_token(self, user_id: str, token: str) -> bool:
        """Deactivate a single token owned by the user. Returns True if a row changed."""
        return self._deactivate_many(user_id, [token]) > 0

    def deactivate_tokens(self, tokens: list[str]) -> int:
        """Deactivate tokens globally, ignoring user ownership.

        Used by the sender when FCM reports a token as permanently invalid: the
        token is dead for the device regardless of which account it was
        registered under, and the caller holds no user id for it.
        """
        if not tokens:
            return 0

        if self._client is None:
            changed = 0
            for token in tokens:
                device = self._local_devices.get(token)
                if device is not None and device.get("active"):
                    device["active"] = False
                    changed += 1
            return changed

        try:
            result = (
                self._client.table(self._table())
                .update({"active": False})
                .in_("token", tokens)
                .execute()
            )
        except APIError as exc:
            raise DeviceRepositoryError(f"Supabase device deactivate failed: {exc.message}") from exc

        return len(result.data or [])

    def _deactivate_many(self, user_id: str, tokens: list[str]) -> int:
        if not tokens:
            return 0

        if self._client is None:
            changed = 0
            for token in tokens:
                device = self._local_devices.get(token)
                if device is not None and device.get("user_id") == user_id and device.get("active"):
                    device["active"] = False
                    changed += 1
            return changed

        try:
            result = (
                self._client.table(self._table())
                .update({"active": False})
                .eq("user_id", user_id)
                .in_("token", tokens)
                .execute()
            )
        except APIError as exc:
            raise DeviceRepositoryError(f"Supabase device deactivate failed: {exc.message}") from exc

        return len(result.data or [])

    def log_notification(self, user_id: str, kind: str, entry_id: str | None = None) -> None:
        """Record that a notification was attempted. Never raises."""
        payload = {
            "id": str(uuid4()),
            "user_id": user_id,
            "kind": kind,
            "entry_id": entry_id,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }

        if self._client is None:
            self._local_notification_log.append(payload)
            return

        try:
            self._client.table(self._log_table()).insert(payload).execute()
        except APIError as exc:
            # The log is an audit trail, not a correctness dependency — a failed
            # write must not fail the notification that triggered it.
            logger.warning(
                "notification_log_write_failed",
                extra={"user_id": user_id, "kind": kind, "error": exc.message},
            )
