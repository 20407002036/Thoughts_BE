import json
import logging

from groq import Groq
from pydantic import ValidationError

from app.core.settings import Settings
from app.models.schemas import JournalAnalysis

logger = logging.getLogger(__name__)


class AnalysisError(RuntimeError):
    """Raised when LLM analysis cannot be completed successfully."""


class AnalysisService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Groq | None = Groq(api_key=settings.groq_api_key) if settings.groq_api_key else None

    def analyze(self, transcript: str) -> JournalAnalysis:
        if self._client is None:
            return JournalAnalysis(
                mood="reflective",
                title="Local Draft Entry",
                summary=transcript[:180] if transcript else "No transcript available",
                themes=["journal", "reflection"],
                insights=["Connect Groq API key to enable model analysis"],
            )

        prompt = (
            "You are a journaling analysis assistant. "
            "Return strict JSON with keys: mood, title, summary, themes, insights. "
            "themes and insights must be arrays of strings."
        )

        try:
            response = self._client.chat.completions.create(
                model=self._settings.groq_llm_model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": transcript},
                ],
            )
        except Exception as exc:
            logger.exception("Groq analysis request failed")
            raise AnalysisError("Failed to analyze transcript") from exc

        choices = getattr(response, "choices", None)
        if not choices:
            raise AnalysisError("Model response did not match expected analysis schema")

        first_choice = choices[0]
        message = getattr(first_choice, "message", None)
        raw = getattr(message, "content", None) or "{}"
        try:
            return JournalAnalysis.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise AnalysisError("Model response did not match expected analysis schema") from exc
