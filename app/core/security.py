import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from app.core.settings import Settings, get_settings

logger = logging.getLogger(__name__)
bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class AuthenticatedUser:
    user_id: str
    email: str | None = None


@lru_cache
def _get_jwk_client(jwks_url: str) -> PyJWKClient:
    return PyJWKClient(jwks_url)


def _decode_with_jwks(token: str, settings: Settings) -> dict[str, Any]:
    if not settings.supabase_jwks_url:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Supabase URL is not configured")

    signing_key = _get_jwk_client(settings.supabase_jwks_url).get_signing_key_from_jwt(token)
    decode_kwargs: dict[str, Any] = {
        # Supabase can issue asymmetric JWTs with either ES256 or RS256.
        "algorithms": ["ES256", "RS256"],
        "audience": settings.supabase_jwt_audience,
        "options": {"verify_aud": True},
    }
    if settings.supabase_jwt_issuer:
        decode_kwargs["issuer"] = settings.supabase_jwt_issuer

    return jwt.decode(
        token,
        signing_key.key,
        **decode_kwargs,
    )


def _decode_hs256(token: str, settings: Settings) -> dict[str, Any]:
    if not settings.supabase_jwt_secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SUPABASE_JWT_SECRET is required for HS256 token verification",
        )

    decode_kwargs: dict[str, Any] = {
        "algorithms": ["HS256"],
        "audience": settings.supabase_jwt_audience,
        "options": {"verify_aud": True},
    }
    if settings.supabase_jwt_issuer:
        decode_kwargs["issuer"] = settings.supabase_jwt_issuer

    return jwt.decode(
        token,
        settings.supabase_jwt_secret,
        **decode_kwargs,
    )


def _decode_token(token: str, settings: Settings) -> dict[str, Any]:
    header = jwt.get_unverified_header(token)
    alg = header.get("alg")

    if alg == "HS256":
        return _decode_hs256(token, settings)

    return _decode_with_jwks(token, settings)


def _extract_user(claims: dict[str, Any]) -> AuthenticatedUser:
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing subject claim")
    return AuthenticatedUser(user_id=user_id, email=claims.get("email"))


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> AuthenticatedUser:
    if not settings.auth_required:
        if settings.supabase_url and settings.supabase_service_role_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "AUTH_REQUIRED=false is incompatible with Supabase persistence. "
                    "Use AUTH_REQUIRED=true with a valid Supabase bearer token, "
                    "or unset Supabase credentials for full local fallback mode."
                ),
            )
        return AuthenticatedUser(user_id="dev-local-user", email="dev@example.com")

    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    token = credentials.credentials
    try:
        claims = _decode_token(token, settings)
    except jwt.PyJWTError as exc:
        logger.warning("JWT validation failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

    return _extract_user(claims)
