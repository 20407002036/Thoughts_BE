from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.rate_limit import rate_limit
from app.core.security import AuthenticatedUser, get_current_user
from app.core.settings import Settings, get_settings
from app.models.schemas import (
    ErrorResponse,
    RegisterDeviceRequest,
    RegisterDeviceResponse,
    TestPushResponse,
    UnregisterDeviceResponse,
)
from app.repositories.device_repository import DeviceRepository, DeviceRepositoryError
from app.repositories.profile_repository import ProfileRepository
from app.services.push_service import PushService

router = APIRouter(prefix="/v1/devices", tags=["devices"])


@lru_cache
def _build_device_repository() -> DeviceRepository:
    return DeviceRepository(settings=get_settings())


@lru_cache
def _build_profile_repository() -> ProfileRepository:
    return ProfileRepository(settings=get_settings())


@lru_cache
def _build_push_service() -> PushService:
    settings = get_settings()
    return PushService(
        settings=settings,
        device_repository=_build_device_repository(),
        profile_repository=_build_profile_repository(),
    )


def get_device_repository(settings: Settings = Depends(get_settings)) -> DeviceRepository:
    _ = settings
    return _build_device_repository()


def get_push_service(settings: Settings = Depends(get_settings)) -> PushService:
    _ = settings
    return _build_push_service()


@router.post(
    "",
    response_model=RegisterDeviceResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_429_TOO_MANY_REQUESTS: {"model": ErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
    },
)
def register_device(
    payload: RegisterDeviceRequest,
    current_user: AuthenticatedUser = Depends(
        rate_limit(key_prefix="device_register", requests=20, window_seconds=60)
    ),
    device_repository: DeviceRepository = Depends(get_device_repository),
) -> RegisterDeviceResponse:
    """Register or refresh this device's FCM token.

    Idempotent: the mobile client calls this on every launch and whenever the
    FCM SDK hands back a refreshed token, so a repeat call for the same token
    must succeed and must not create a second row. A token that was previously
    registered to a different account is reassigned to the caller.
    """
    try:
        row = device_repository.register_device(
            user_id=current_user.user_id,
            token=payload.token,
            platform=payload.platform,
            app_version=payload.app_version,
        )
    except DeviceRepositoryError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return RegisterDeviceResponse(
        device_id=str(row.get("id", "")),
        platform=str(row.get("platform", payload.platform)),
        registered=True,
    )


@router.delete(
    "/{token}",
    response_model=UnregisterDeviceResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_429_TOO_MANY_REQUESTS: {"model": ErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
    },
)
def unregister_device(
    token: str,
    current_user: AuthenticatedUser = Depends(
        rate_limit(key_prefix="device_unregister", requests=20, window_seconds=60)
    ),
    device_repository: DeviceRepository = Depends(get_device_repository),
) -> UnregisterDeviceResponse:
    """Stop push to this device.

    Call on sign-out and when the user disables notifications at the OS level.
    Scoped to the calling user, so one account cannot unregister another's
    device. Idempotent: an unknown token returns 200 with removed=false rather
    than 404, because sign-out runs on clients that may never have registered.
    """
    try:
        removed = device_repository.deactivate_token(user_id=current_user.user_id, token=token)
    except DeviceRepositoryError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return UnregisterDeviceResponse(success=True, removed=removed)


@router.post(
    "/test",
    response_model=TestPushResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_429_TOO_MANY_REQUESTS: {"model": ErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
    },
)
def send_test_push(
    current_user: AuthenticatedUser = Depends(
        rate_limit(key_prefix="device_test_push", requests=3, window_seconds=60)
    ),
    push_service: PushService = Depends(get_push_service),
) -> TestPushResponse:
    """Send a fixed diagnostic notification to the caller's own devices.

    Lets the client confirm end-to-end delivery (permission granted, token
    registered, project wired up) without waiting for a real event. The body is
    static — it never carries journal content.
    """
    result = push_service.send_test(current_user.user_id)
    return TestPushResponse(
        status=result.status,
        sent=result.sent,
        failed=result.failed,
        reason=result.reason,
    )
