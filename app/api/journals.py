from functools import lru_cache

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect, status

from app.core.security import AuthenticatedUser, get_current_user
from app.core.settings import Settings, get_settings
from app.models.schemas import (
    ErrorResponse,
    JournalEntryDetail,
    JournalEntryListResponse,
    JournalEntryResponse,
    UnsupportedActionResponse,
    UpdateJournalEntryRequest,
)
from app.repositories.journal_repository import JournalRepository, JournalRepositoryError
from app.services.analysis_service import AnalysisError, AnalysisService
from app.services.journal_service import JournalNotFoundError, JournalService, JournalValidationError
from app.services.journal_pipeline import JournalPipeline, PipelineTimeoutError
from app.services.live_transcription_service import LiveTranscriptionError, LiveTranscriptionService
from app.services.storage_service import StorageService
from app.services.transcription_service import TranscriptionError, TranscriptionService

router = APIRouter(prefix="/v1/journals", tags=["journals"])
entries_router = APIRouter(prefix="/v1/entries", tags=["entries"])


@lru_cache
def _build_journal_repository() -> JournalRepository:
    return JournalRepository(get_settings())


@lru_cache
def _build_pipeline() -> JournalPipeline:
    settings = get_settings()
    return JournalPipeline(
        settings=settings,
        storage_service=StorageService(settings),
        transcription_service=TranscriptionService(settings),
        analysis_service=AnalysisService(settings),
        journal_repository=_build_journal_repository(),
    )


def get_pipeline(settings: Settings = Depends(get_settings)) -> JournalPipeline:
    # Include settings dependency so future env reload strategy can invalidate cache if needed.
    _ = settings
    return _build_pipeline()


@lru_cache
def _build_journal_service() -> JournalService:
    return JournalService(journal_repository=_build_journal_repository())


def get_journal_service(settings: Settings = Depends(get_settings)) -> JournalService:
    _ = settings
    return _build_journal_service()


@router.post(
    "/ingest",
    response_model=JournalEntryResponse,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
        status.HTTP_504_GATEWAY_TIMEOUT: {"model": ErrorResponse},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
    },
)
async def ingest_journal_audio(
    audio: UploadFile = File(...),
    current_user: AuthenticatedUser = Depends(get_current_user),
    pipeline: JournalPipeline = Depends(get_pipeline),
) -> JournalEntryResponse:
    try:
        return await pipeline.process_upload(user_id=current_user.user_id, audio_file=audio)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except AnalysisError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except TranscriptionError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except JournalRepositoryError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except PipelineTimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected processing failure",
        ) from exc


def _map_journal_repository_error(exc: JournalRepositoryError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.get(
    "",
    response_model=JournalEntryListResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
    },
)
@entries_router.get(
    "",
    response_model=JournalEntryListResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
    },
)
def list_journal_entries(
    current_user: AuthenticatedUser = Depends(get_current_user),
    journal_service: JournalService = Depends(get_journal_service),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    month: str | None = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    query: str | None = Query(default=None, min_length=1, max_length=120),
    tag: str | None = Query(default=None, min_length=1, max_length=80),
) -> JournalEntryListResponse:
    try:
        return journal_service.list_entries(
            user_id=current_user.user_id,
            limit=limit,
            offset=offset,
            month=month,
            query=query,
            tag=tag,
        )
    except JournalRepositoryError as exc:
        raise _map_journal_repository_error(exc) from exc


@router.get(
    "/{entry_id}",
    response_model=JournalEntryDetail,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
    },
)
@entries_router.get(
    "/{entry_id}",
    response_model=JournalEntryDetail,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
    },
)
def get_journal_entry(
    entry_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    journal_service: JournalService = Depends(get_journal_service),
) -> JournalEntryDetail:
    try:
        return journal_service.get_entry(user_id=current_user.user_id, entry_id=entry_id)
    except JournalNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except JournalRepositoryError as exc:
        raise _map_journal_repository_error(exc) from exc


@router.patch(
    "/{entry_id}",
    response_model=JournalEntryDetail,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
    },
)
@entries_router.patch(
    "/{entry_id}",
    response_model=JournalEntryDetail,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
    },
)
def update_journal_entry(
    entry_id: str,
    payload: UpdateJournalEntryRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    journal_service: JournalService = Depends(get_journal_service),
) -> JournalEntryDetail:
    try:
        return journal_service.update_entry(
            user_id=current_user.user_id,
            entry_id=entry_id,
            title=payload.title,
            summary=payload.summary,
            tags=payload.tags,
        )
    except JournalValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except JournalNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except JournalRepositoryError as exc:
        raise _map_journal_repository_error(exc) from exc


@router.post("/{entry_id}/export", response_model=UnsupportedActionResponse)
@entries_router.post("/{entry_id}/export", response_model=UnsupportedActionResponse)
def export_journal_entry(entry_id: str, current_user: AuthenticatedUser = Depends(get_current_user)) -> UnsupportedActionResponse:
    _ = entry_id, current_user
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Journal export is not supported yet",
    )


@router.websocket("/live-transcribe")
async def live_transcribe_websocket(
    websocket: WebSocket,
    settings: Settings = Depends(get_settings),
) -> None:
    """
    WebSocket endpoint for real-time audio transcription using Vosk.

    Protocol:
    1. Client sends binary audio chunks (16-bit PCM, 16kHz recommended)
    2. Server responds with JSON for each chunk:
       {"partial": "text...", "final": null, "is_final": false}  # During transcription
       {"partial": null, "final": "complete text", "is_final": true}  # When phrase complete
    3. Client can send {"action": "stop"} to end session and get final result
    """
    try:
        await websocket.accept()

        # Initialize transcription service
        try:
            service = LiveTranscriptionService(settings)
            recognizer = service.create_recognizer()
        except LiveTranscriptionError as exc:
            await websocket.send_json({"error": str(exc), "code": "init_failed"})
            await websocket.close(code=1008, reason="Failed to initialize transcription")
            return

        try:
            while True:
                # Receive data from client
                data = await websocket.receive()

                # Handle text messages (control commands)
                if "text" in data:
                    message = data["text"]
                    if message == "stop" or message == '{"action": "stop"}':
                        # End session and return final result
                        final_text = service.get_final_result(recognizer)
                        await websocket.send_json({
                            "final": final_text,
                            "is_final": True,
                            "session_ended": True,
                        })
                        break
                    continue

                # Handle binary audio chunks
                if "bytes" in data:
                    audio_chunk = data["bytes"]
                    try:
                        result = LiveTranscriptionService.process_chunk(recognizer, audio_chunk)
                        await websocket.send_json(result)
                    except LiveTranscriptionError as exc:
                        await websocket.send_json({
                            "error": str(exc),
                            "code": "process_failed",
                        })
                        break

        except WebSocketDisconnect:
            pass  # Client disconnected
        except Exception as exc:
            import logging
            logging.exception("Unexpected error in live transcription websocket")
            try:
                await websocket.send_json({
                    "error": "An unexpected error occurred",
                    "code": "internal_error",
                })
            except Exception:
                pass  # Already disconnected or sending failed
            finally:
                await websocket.close(code=1011, reason="Internal server error")

    except Exception as exc:
        import logging
        logging.exception("WebSocket connection error")
        try:
            await websocket.close(code=1011, reason="Connection error")
        except Exception:
            pass




@router.post("/{entry_id}/share", response_model=UnsupportedActionResponse)
@entries_router.post("/{entry_id}/share", response_model=UnsupportedActionResponse)
def share_journal_entry(entry_id: str, current_user: AuthenticatedUser = Depends(get_current_user)) -> UnsupportedActionResponse:
    _ = entry_id, current_user
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Journal sharing is not supported yet",
    )
