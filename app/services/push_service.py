"""Event-driven push delivery over Firebase Cloud Messaging.

Only event-driven notifications are wired up: a push is triggered by something
that already happened in the product (today, "your journal finished
processing"). Nothing here schedules work on a clock.

Two rules shape the design:

1. **Never fail the caller.** Every method swallows FCM errors and returns a
   result object. A push is a side effect of an ingest that already succeeded;
   a Firebase outage must not roll back a journal entry or fail an HTTP
   request. This mirrors the best-effort streak update in `journal_pipeline`.

2. **Never put user content in a push.** Notification bodies are fixed
   strings; the only variable is the entry id, carried in `data`. That keeps
   journal text (which is encrypted at rest) off Google's servers and off the
   device lock screen.

`firebase_admin` is imported lazily so the API, the worker, and the test suite
all import cleanly without the dependency installed and without the Admin SDK
initialising a global app at import time.
"""
from __future__ import annotations

import logging
from typing import Any

from app.core.settings import Settings
from app.repositories.device_repository import DeviceRepository
from app.repositories.profile_repository import ProfileRepository

logger = logging.getLogger(__name__)

# How a failed send is classified. Only "dead_token" prunes the registration.
#
# Classification is by exception *type*, not by the error's `code` string. The
# Admin SDK maps FCM responses onto gRPC-style status codes, so `UNREGISTERED`
# surfaces as `NotFoundError.code == "NOT_FOUND"` and a genuine 404 is
# indistinguishable by string. The concrete subclasses are the reliable
# signal: `UnregisteredError` (app uninstalled / token deleted) and
# `SenderIdMismatchError` (token minted by a different Firebase project) both
# mean this specific device can never be reached again.
#
# Notably NOT in the dead-token set: `ThirdPartyAuthError` / APNS auth
# failures and `QuotaExceededError`, which are server-side misconfiguration or
# throttling — pruning tokens for those would silently disable push for every
# user during an outage.
SEND_ERROR_DEAD_TOKEN = "dead_token"
SEND_ERROR_RETRY = "retry"
SEND_ERROR_UNKNOWN = "unknown"

KIND_JOURNAL_READY = "journal_ready"

JOURNAL_READY_TITLE = "Your journal is ready"
JOURNAL_READY_BODY = "Tap to read what you just recorded."


class PushResult:
    """Outcome of one send attempt. `skipped` explains a deliberate no-op."""

    def __init__(
        self,
        *,
        status: str,
        sent: int = 0,
        failed: int = 0,
        deactivated: int = 0,
        reason: str | None = None,
    ) -> None:
        self.status = status
        self.sent = sent
        self.failed = failed
        self.deactivated = deactivated
        self.reason = reason

    @property
    def skipped(self) -> bool:
        return self.status == "skipped"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "sent": self.sent,
            "failed": self.failed,
            "deactivated": self.deactivated,
            "reason": self.reason,
        }


class PushService:
    def __init__(
        self,
        settings: Settings,
        device_repository: DeviceRepository,
        profile_repository: ProfileRepository,
    ) -> None:
        self._settings = settings
        self._devices = device_repository
        self._profiles = profile_repository

    def notify_journal_ready(self, user_id: str, entry_id: str) -> PushResult:
        return self.send_to_user(
            user_id=user_id,
            title=JOURNAL_READY_TITLE,
            body=JOURNAL_READY_BODY,
            data={"type": KIND_JOURNAL_READY, "entry_id": entry_id},
            kind=KIND_JOURNAL_READY,
            entry_id=entry_id,
        )

    def send_to_user(
        self,
        user_id: str,
        *,
        title: str,
        body: str,
        data: dict[str, str] | None = None,
        kind: str,
        entry_id: str | None = None,
    ) -> PushResult:
        """Deliver a notification to every active device registered to the user.

        Returns a PushResult instead of raising. Callers on the request path
        can ignore it; callers in the worker can log it.
        """
        if not self._settings.firebase_configured:
            return PushResult(status="skipped", reason="firebase_not_configured")

        try:
            if not self._profiles.get_profile(user_id).get("notifications_enabled", True):
                return PushResult(status="skipped", reason="notifications_disabled")
        except Exception as exc:
            # A profile read failure must not silently drop the notification.
            logger.warning(
                "push_preference_check_failed",
                extra={"user_id": user_id, "kind": kind, "error": str(exc)},
                exc_info=True,
            )
            return PushResult(status="skipped", reason="preference_check_failed")

        try:
            tokens = self._devices.list_active_tokens(user_id)
        except Exception as exc:
            logger.error(
                "push_token_lookup_failed",
                extra={"user_id": user_id, "kind": kind, "error": str(exc)},
                exc_info=True,
            )
            return PushResult(status="failed", reason="token_lookup_failed")

        if not tokens:
            self._devices.log_notification(user_id=user_id, kind=kind, entry_id=entry_id)
            return PushResult(status="skipped", reason="no_active_devices")

        self._devices.log_notification(user_id=user_id, kind=kind, entry_id=entry_id)
        return self._dispatch(user_id=user_id, tokens=tokens, title=title, body=body, data=data or {}, kind=kind)

    def _dispatch(
        self,
        user_id: str,
        *,
        tokens: list[str],
        title: str,
        body: str,
        data: dict[str, str],
        kind: str,
    ) -> PushResult:
        try:
            initialize_firebase_app(self._settings)
            messaging = _get_messaging_module()
        except Exception as exc:
            logger.error(
                "push_messaging_unavailable",
                extra={"user_id": user_id, "kind": kind, "error": str(exc)},
                exc_info=True,
            )
            return PushResult(status="failed", reason="messaging_unavailable")

        sent = 0
        failed = 0
        dead: list[str] = []

        # FCM accepts at most 500 tokens per multicast call.
        for offset in range(0, len(tokens), self._settings.fcm_batch_size):
            batch = tokens[offset : offset + self._settings.fcm_batch_size]
            try:
                response = messaging.send_each_for_multicast(
                    messaging.MulticastMessage(
                        tokens=batch,
                        notification=messaging.Notification(title=title, body=body),
                        data=data,
                        android=messaging.AndroidConfig(priority="high"),
                        apns=messaging.APNSConfig(headers={"apns-priority": "10"}),
                    ),
                    dry_run=self._settings.firebase_dry_run,
                )
            except Exception as exc:
                # A whole batch failing is usually auth or quota. Tokens stay
                # active so a later event can retry.
                logger.error(
                    "push_batch_failed",
                    extra={"user_id": user_id, "kind": kind, "batch_size": len(batch), "error": str(exc)},
                    exc_info=True,
                )
                failed += len(batch)
                continue

            # BatchResponse.responses is index-aligned with the tokens that were
            # sent (the SDK builds one Message per token, in order), so the
            # token for a failure has to come from the batch — SendResponse
            # does not carry it.
            for token, send_response in zip(batch, response.responses):
                if send_response.success:
                    sent += 1
                    continue

                failed += 1
                outcome = classify_send_error(messaging, send_response.exception)
                if outcome == SEND_ERROR_UNKNOWN:
                    logger.warning(
                        "push_send_rejected",
                        extra={
                            "user_id": user_id,
                            "kind": kind,
                            "error_type": type(send_response.exception).__name__,
                        },
                    )
                elif outcome == SEND_ERROR_RETRY:
                    logger.info(
                        "push_send_retryable_error",
                        extra={
                            "user_id": user_id,
                            "kind": kind,
                            "error_type": type(send_response.exception).__name__,
                        },
                    )
                if outcome == SEND_ERROR_DEAD_TOKEN and token not in dead:
                    dead.append(token)

        deactivated = 0
        if dead:
            try:
                deactivated = self._devices.deactivate_tokens(dead)
            except Exception as exc:
                logger.warning(
                    "push_token_prune_failed",
                    extra={"user_id": user_id, "kind": kind, "error": str(exc)},
                    exc_info=True,
                )

        logger.info(
            "push_send_completed",
            extra={
                "user_id": user_id,
                "kind": kind,
                "device_count": len(tokens),
                "sent": sent,
                "failed": failed,
                "deactivated": deactivated,
            },
        )
        return PushResult(status="sent", sent=sent, failed=failed, deactivated=deactivated)

    def send_test(self, user_id: str) -> PushResult:
        """Deliver a fixed diagnostic push. Used by POST /v1/devices/test."""
        return self.send_to_user(
            user_id=user_id,
            title="Mindful Moments",
            body="Push notifications are connected.",
            data={"type": "test"},
            kind="test",
        )


_app = None


def initialize_firebase_app(settings: Settings):
    """Initialise the firebase_admin app once per process.

    firebase_admin keeps a module-level singleton keyed by app name and raises
    if the default app is initialised twice, which happens naturally in the
    worker (many task invocations) and in tests that build services repeatedly.
    Reusing the existing app keeps that from turning into a crash.
    """
    global _app
    import firebase_admin
    from firebase_admin import credentials

    if _app is not None:
        return _app

    if settings.firebase_credentials is not None:
        credential = credentials.Certificate(settings.firebase_credentials)
    elif settings.firebase_credentials_path:
        credential = credentials.Certificate(settings.firebase_credentials_path)
    else:
        # No explicit credential: fall back to Application Default Credentials.
        credential = credentials.ApplicationDefault()

    try:
        _app = firebase_admin.get_app()
    except ValueError:
        _app = firebase_admin.initialize_app(credential, {"projectId": settings.firebase_project_id})

    return _app


def _get_messaging_module():
    from firebase_admin import messaging

    return messaging


def classify_send_error(messaging, exception: BaseException | None) -> str:
    """Classify a per-message send failure as dead_token, retry, or unknown.

    Takes the messaging module as an argument so the mapping can be exercised
    against the real firebase_admin exception types in tests without any
    initialised app or network access.
    """
    if exception is None:
        return SEND_ERROR_UNKNOWN

    if isinstance(exception, (messaging.UnregisteredError, messaging.SenderIdMismatchError)):
        return SEND_ERROR_DEAD_TOKEN

    from firebase_admin import exceptions

    if isinstance(
        exception,
        (
            messaging.QuotaExceededError,
            messaging.ThirdPartyAuthError,
            exceptions.InternalError,
            exceptions.UnavailableError,
            exceptions.DeadlineExceededError,
            exceptions.ResourceExhaustedError,
        ),
    ):
        return SEND_ERROR_RETRY

    return SEND_ERROR_UNKNOWN
