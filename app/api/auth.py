from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials

from app.core.security import bearer_scheme
from app.core.settings import Settings, get_settings
from app.models.schemas import (
    ErrorResponse,
    LogoutResponse,
    RefreshTokenRequest,
    RefreshTokenResponse,
    SignInRequest,
    SignInResponse,
    SignUpRequest,
    SignUpResponse,
)
from app.services.auth_service import (
    AuthConflictError,
    AuthInvalidCredentialsError,
    AuthService,
    AuthServiceConfigError,
    AuthUnauthorizedError,
    AuthUpstreamError,
    AuthValidationError,
)

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@lru_cache
def _build_auth_service() -> AuthService:
    settings = get_settings()
    return AuthService(settings=settings)


def get_auth_service(settings: Settings = Depends(get_settings)) -> AuthService:
    _ = settings
    return _build_auth_service()


@router.post(
    "/login",
    response_model=SignInResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
    },
)
def sign_in(
    payload: SignInRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> SignInResponse:
    try:
        token_payload = auth_service.sign_in(email=payload.email, password=payload.password)
    except AuthServiceConfigError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    except AuthInvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except AuthUpstreamError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    user: dict[str, Any] = token_payload.get("user") if isinstance(token_payload, dict) else {}
    return SignInResponse(
        access_token=token_payload["access_token"],
        token_type=token_payload.get("token_type", "bearer"),
        expires_in=token_payload.get("expires_in"),
        refresh_token=token_payload.get("refresh_token"),
        user_id=user.get("id"),
        email=user.get("email"),
    )


@router.post(
    "/signup",
    response_model=SignUpResponse,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
    },
)
def sign_up(
    payload: SignUpRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> SignUpResponse:
    try:
        sign_up_payload = auth_service.sign_up(email=payload.email, password=payload.password)
    except AuthServiceConfigError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    except AuthValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except AuthConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except AuthUpstreamError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return SignUpResponse(**sign_up_payload)


@router.post(
    "/refresh",
    response_model=RefreshTokenResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
    },
)
def refresh_token(
    payload: RefreshTokenRequest,
    auth_service: AuthService = Depends(get_auth_service),
) -> RefreshTokenResponse:
    try:
        token_payload = auth_service.refresh_session(refresh_token=payload.refresh_token)
    except AuthServiceConfigError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    except AuthUnauthorizedError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except AuthUpstreamError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    user: dict[str, Any] = token_payload.get("user") if isinstance(token_payload, dict) else {}
    return RefreshTokenResponse(
        access_token=token_payload["access_token"],
        token_type=token_payload.get("token_type", "bearer"),
        expires_in=token_payload.get("expires_in"),
        refresh_token=token_payload.get("refresh_token"),
        user_id=user.get("id"),
        email=user.get("email"),
    )


@router.post(
    "/logout",
    response_model=LogoutResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
    },
)
def sign_out(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    auth_service: AuthService = Depends(get_auth_service),
) -> LogoutResponse:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    try:
        auth_service.sign_out(access_token=credentials.credentials)
    except AuthServiceConfigError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    except AuthUnauthorizedError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except AuthUpstreamError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return LogoutResponse(success=True)
