import asyncio
import io
import wave
import logging
from datetime import datetime, timezone
from fastapi import UploadFile

from app.core.settings import Settings
from app.models.schemas import ChallengeAttemptMetrics, ChallengeAttemptResponse, ChallengeResponse, FillerWordMetric
from app.repositories.challenge_repository import ChallengeRepository
from app.services.challenge_analysis_service import ChallengeAnalysisService
from app.services.speech_analytics import SpeechAnalytics
from app.services.storage_service import StorageService
from app.services.transcription_service import TranscriptionService

logger = logging.getLogger(__name__)


class ChallengePipelineError(RuntimeError):
    """Raised when a challenge pipeline operation fails."""


def estimate_audio_duration(content: bytes, filename: str | None) -> float:
    # Try parsing as WAV
    try:
        with wave.open(io.BytesIO(content), "rb") as wav:
            frames = wav.getnframes()
            rate = wav.getframerate()
            return frames / float(rate)
    except Exception:
        pass

    # Fallback estimate (approx. 16 KB/s for compressed mono audio or 32 KB/s for uncompressed speech WAV)
    is_wav = filename.lower().endswith(".wav") if filename else False
    rate_bytes_per_sec = 32000.0 if is_wav else 16000.0
    return max(1.0, len(content) / rate_bytes_per_sec)


class ChallengePipeline:
    def __init__(
        self,
        settings: Settings,
        storage_service: StorageService,
        transcription_service: TranscriptionService,
        challenge_analysis_service: ChallengeAnalysisService,
        challenge_repository: ChallengeRepository,
    ) -> None:
        self._settings = settings
        self._storage = storage_service
        self._transcription = transcription_service
        self._analysis = challenge_analysis_service
        self._repository = challenge_repository

    async def process_attempt(
        self,
        user_id: str,
        challenge_id: str,
        audio_file: UploadFile,
        duration_seconds: float | None = None,
    ) -> ChallengeAttemptResponse:
        challenge_data = self._repository.get_challenge(challenge_id)
        if not challenge_data:
            raise ValueError(f"Challenge with ID {challenge_id} not found")

        content_type = audio_file.content_type or ""
        if not content_type.startswith("audio/"):
            raise ValueError("Uploaded file must be an audio format")

        content = await audio_file.read()
        if not content:
            raise ValueError("Uploaded audio file is empty")

        max_bytes = self._settings.max_upload_mb * 1024 * 1024
        if len(content) > max_bytes:
            raise ValueError(f"Audio exceeds max size of {self._settings.max_upload_mb} MB")

        # Estimate duration if client didn't supply it
        if duration_seconds is None or duration_seconds <= 0:
            duration_seconds = estimate_audio_duration(content, audio_file.filename)

        # Upload audio asynchronously
        audio_path, signed_url = await self._run_with_timeout(
            "upload_audio",
            self._storage.upload_audio,
            user_id=user_id,
            filename=audio_file.filename,
            content=content,
            content_type=content_type,
        )

        # Transcribe audio using Whisper
        transcript = await self._run_with_timeout(
            "transcribe",
            self._transcription.transcribe,
            filename=audio_file.filename,
            content=content,
        )

        # Calculate metrics locally
        word_count = len(transcript.split())
        wpm = SpeechAnalytics.calculate_wpm(word_count, duration_seconds)
        filler_words_count, filler_words_breakdown = SpeechAnalytics.analyze_filler_words(transcript)

        metrics = {
            "duration_seconds": duration_seconds,
            "word_count": word_count,
            "wpm": wpm,
            "filler_words_count": filler_words_count,
            "filler_words_breakdown": filler_words_breakdown,
        }

        # Analyze using LLM Coach Vinh Giang
        evaluation = await self._run_with_timeout(
            "analyze_attempt",
            self._analysis.analyze_attempt,
            transcript=transcript,
            prompt_text=challenge_data["prompt_text"],
            vocal_goal=challenge_data["vocal_goal"],
            metrics=metrics,
        )

        # Build payload and persist attempt
        db_payload = {
            "challenge_id": challenge_id,
            "user_id": user_id,
            "audio_path": audio_path,
            "transcript": transcript,
            "duration_seconds": duration_seconds,
            "word_count": word_count,
            "wpm": wpm,
            "filler_words_count": filler_words_count,
            "score": evaluation.score,
            "coach_feedback": evaluation.model_dump(),
        }

        saved = await self._run_with_timeout(
            "create_attempt",
            self._repository.create_attempt,
            payload=db_payload,
        )

        attempt_id = str(saved.get("id", ""))
        
        return ChallengeAttemptResponse(
            attempt_id=attempt_id,
            challenge_id=challenge_id,
            metrics=ChallengeAttemptMetrics(
                duration_seconds=duration_seconds,
                word_count=word_count,
                wpm=wpm,
                filler_words_count=filler_words_count,
            ),
            evaluation=evaluation,
        )

    async def _run_with_timeout(self, stage: str, func, *args, **kwargs):
        timeout_seconds = self._settings.request_timeout_seconds
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(func, *args, **kwargs),
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            logger.warning(
                "challenge_pipeline_stage_timeout",
                extra={"stage": stage, "timeout_seconds": timeout_seconds},
            )
            raise ChallengePipelineError(f"Pipeline stage '{stage}' timed out") from exc
