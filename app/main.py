import asyncio
import logging
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.journals import router as journals_router
from app.api.profile import router as profile_router
from app.core.logging import configure_logging, get_correlation_id, set_correlation_id
from app.core.settings import get_settings

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.app_name)
app.add_middleware(
	CORSMiddleware,
	allow_origins=settings.cors_origins,
	allow_credentials=settings.cors_allow_credentials,
	allow_methods=settings.cors_methods,
	allow_headers=settings.cors_headers,
)
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(journals_router)
app.include_router(profile_router)


def _error_payload(error: str, message: str) -> dict[str, str]:
	return {
		"error": error,
		"message": message,
		"correlation_id": get_correlation_id(),
	}


def _error_response(status_code: int, error: str, message: str) -> JSONResponse:
	return JSONResponse(
		status_code=status_code,
		content=_error_payload(error=error, message=message),
		headers={"X-Correlation-ID": get_correlation_id()},
	)


@app.middleware("http")
async def request_middleware(request: Request, call_next):
	correlation_id = request.headers.get("X-Correlation-ID") or str(uuid4())
	set_correlation_id(correlation_id)
	content_length_header = request.headers.get("content-length")
	if content_length_header and request.method in {"POST", "PUT", "PATCH"}:
		try:
			content_length = int(content_length_header)
		except ValueError:
			content_length = 0

		max_bytes = settings.max_upload_mb * 1024 * 1024
		if content_length > max_bytes:
			logger.warning(
				"request_content_length_exceeded",
				extra={
					"path": request.url.path,
					"method": request.method,
					"content_length": content_length,
					"max_bytes": max_bytes,
				},
			)
			return _error_response(
				status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
				error="payload_too_large",
				message=f"Request exceeds max size of {settings.max_upload_mb} MB",
			)

	logger.info(
		"request_started",
		extra={
			"path": request.url.path,
			"method": request.method,
		},
	)
	try:
		response = await asyncio.wait_for(call_next(request), timeout=settings.request_timeout_seconds)
	except TimeoutError:
		logger.warning(
			"request_timeout",
			extra={
				"path": request.url.path,
				"method": request.method,
				"timeout_seconds": settings.request_timeout_seconds,
			},
		)
		return _error_response(
			status_code=status.HTTP_504_GATEWAY_TIMEOUT,
			error="request_timeout",
			message="Request timed out",
		)

	response.headers["X-Correlation-ID"] = correlation_id
	logger.info(
		"request_completed",
		extra={
			"path": request.url.path,
			"method": request.method,
			"status_code": response.status_code,
		},
	)
	return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
	if isinstance(exc.detail, str):
		message = exc.detail
	else:
		message = "Request failed"

	error = {
		status.HTTP_400_BAD_REQUEST: "bad_request",
		status.HTTP_401_UNAUTHORIZED: "unauthorized",
		status.HTTP_409_CONFLICT: "conflict",
		status.HTTP_404_NOT_FOUND: "not_found",
		status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: "payload_too_large",
		status.HTTP_422_UNPROCESSABLE_ENTITY: "validation_error",
		status.HTTP_502_BAD_GATEWAY: "upstream_error",
		status.HTTP_504_GATEWAY_TIMEOUT: "request_timeout",
		status.HTTP_500_INTERNAL_SERVER_ERROR: "internal_error",
	}.get(exc.status_code, "http_error")

	logger.warning(
		"http_exception",
		extra={
			"path": request.url.path,
			"method": request.method,
			"status_code": exc.status_code,
			"error_type": error,
			"error_message": message,
		},
	)
	return _error_response(status_code=exc.status_code, error=error, message=message)


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
	logger.warning(
		"request_validation_error",
		extra={
			"path": request.url.path,
			"method": request.method,
			"errors": exc.errors(),
		},
	)
	return _error_response(
		status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
		error="validation_error",
		message="Request validation failed",
	)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
	logger.exception(
		"unhandled_exception",
		extra={
			"path": request.url.path,
			"method": request.method,
		},
	)
	return _error_response(
		status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
		error="internal_error",
		message="Unexpected server error",
	)
