from typing import Any

import httpx

from app.core.settings import Settings


class AuthServiceConfigError(RuntimeError):
    """Raised when auth service configuration is incomplete."""


class AuthInvalidCredentialsError(RuntimeError):
    """Raised when provided credentials are not accepted by Supabase."""


class AuthValidationError(RuntimeError):
    """Raised when auth input fails validation constraints."""


class AuthConflictError(RuntimeError):
    """Raised when signup conflicts with existing user state."""


class AuthUnauthorizedError(RuntimeError):
    """Raised when auth token is invalid or expired."""


class AuthUpstreamError(RuntimeError):
    """Raised when Supabase auth cannot be reached or returns an unexpected response."""


class AuthService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _require_supabase(self) -> None:
        if not self._settings.supabase_url or not self._settings.supabase_anon_key:
            raise AuthServiceConfigError(
                "Supabase auth requires SUPABASE_URL and SUPABASE_ANON_KEY"
            )

    @property
    def _auth_base_url(self) -> str:
        return f"{self._settings.supabase_url.rstrip('/')}/auth/v1"

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        access_token: str | None = None,
    ) -> httpx.Response:
        headers = {
            "apikey": self._settings.supabase_anon_key,
            "Content-Type": "application/json",
        }
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        try:
            return httpx.request(
                method,
                f"{self._auth_base_url}{path}",
                params=params,
                headers=headers,
                json=payload,
                timeout=self._settings.request_timeout_seconds,
            )
        except httpx.RequestError as exc:
            raise AuthUpstreamError("Unable to reach Supabase auth service") from exc

    @staticmethod
    def _parse_json_response(response: httpx.Response, context: str) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise AuthUpstreamError(f"Supabase {context} returned malformed JSON") from exc

        if not isinstance(body, dict):
            raise AuthUpstreamError(f"Supabase {context} returned unexpected payload")

        return body

    @staticmethod
    def _extract_session_payload(body: dict[str, Any], *, token_required: bool) -> dict[str, Any]:
        access_token = body.get("access_token")
        if token_required and not access_token:
            raise AuthUpstreamError("Supabase auth response missing access token")

        user: dict[str, Any] = body.get("user") if isinstance(body.get("user"), dict) else {}
        return {
            "access_token": access_token,
            "token_type": body.get("token_type", "bearer"),
            "expires_in": body.get("expires_in"),
            "refresh_token": body.get("refresh_token"),
            "user_id": user.get("id"),
            "email": user.get("email"),
        }

    def sign_in(self, email: str, password: str) -> dict[str, Any]:
        self._require_supabase()
        response = self._request(
            "POST",
            "/token",
            params={"grant_type": "password"},
            payload={"email": email, "password": password},
        )

        if response.status_code in {400, 401}:
            raise AuthInvalidCredentialsError("Invalid email or password")

        if response.status_code >= 500:
            raise AuthUpstreamError("Supabase auth service is unavailable")

        if response.status_code >= 400:
            raise AuthUpstreamError("Supabase sign-in failed")

        body = self._parse_json_response(response, "sign-in")
        session_payload = self._extract_session_payload(body, token_required=True)
        return {
            "access_token": session_payload["access_token"],
            "token_type": session_payload["token_type"],
            "expires_in": session_payload["expires_in"],
            "refresh_token": session_payload["refresh_token"],
            "user": {
                "id": session_payload["user_id"],
                "email": session_payload["email"],
            },
        }

    def sign_up(self, email: str, password: str) -> dict[str, Any]:
        self._require_supabase()
        response = self._request(
            "POST",
            "/signup",
            payload={"email": email, "password": password},
        )

        if response.status_code == 422:
            raise AuthValidationError("Invalid signup payload")

        if response.status_code == 409:
            raise AuthConflictError("User already exists")

        if response.status_code in {400, 401}:
            body = self._parse_json_response(response, "signup")
            message = str(body.get("msg") or body.get("error_description") or body.get("error") or "Signup failed")
            normalized = message.lower()
            if "already" in normalized and "registered" in normalized:
                raise AuthConflictError("User already exists")
            raise AuthValidationError(message)

        if response.status_code >= 500:
            raise AuthUpstreamError("Supabase auth service is unavailable")

        if response.status_code >= 400:
            raise AuthUpstreamError("Supabase signup failed")

        body = self._parse_json_response(response, "signup")
        user: dict[str, Any] = body.get("user") if isinstance(body.get("user"), dict) else {}
        session_payload = self._extract_session_payload(body, token_required=False)

        user_email = user.get("email") or session_payload["email"]
        email_confirmed = bool(user.get("email_confirmed_at") or user.get("confirmed_at"))
        return {
            "user_id": user.get("id") or session_payload["user_id"],
            "email": user_email,
            "email_confirmed": email_confirmed,
            "access_token": session_payload["access_token"],
            "token_type": session_payload["token_type"] if session_payload["access_token"] else None,
            "expires_in": session_payload["expires_in"],
            "refresh_token": session_payload["refresh_token"],
        }

    def refresh_session(self, refresh_token: str) -> dict[str, Any]:
        self._require_supabase()
        response = self._request(
            "POST",
            "/token",
            params={"grant_type": "refresh_token"},
            payload={"refresh_token": refresh_token},
        )

        if response.status_code in {400, 401}:
            raise AuthUnauthorizedError("Invalid refresh token")

        if response.status_code >= 500:
            raise AuthUpstreamError("Supabase auth service is unavailable")

        if response.status_code >= 400:
            raise AuthUpstreamError("Supabase token refresh failed")

        body = self._parse_json_response(response, "token refresh")
        session_payload = self._extract_session_payload(body, token_required=True)
        return {
            "access_token": session_payload["access_token"],
            "token_type": session_payload["token_type"],
            "expires_in": session_payload["expires_in"],
            "refresh_token": session_payload["refresh_token"],
            "user": {
                "id": session_payload["user_id"],
                "email": session_payload["email"],
            },
        }

    def sign_out(self, access_token: str) -> None:
        self._require_supabase()
        response = self._request("POST", "/logout", access_token=access_token)

        if response.status_code in {400, 401, 403}:
            raise AuthUnauthorizedError("Invalid access token")

        if response.status_code >= 500:
            raise AuthUpstreamError("Supabase auth service is unavailable")

        if response.status_code >= 400:
            raise AuthUpstreamError("Supabase logout failed")
