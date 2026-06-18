import json
import logging
from groq import Groq
from pydantic import ValidationError

from app.core.settings import Settings
from app.models.schemas import ChallengeEvaluation, FillerWordMetric

logger = logging.getLogger(__name__)


class ChallengeAnalysisError(RuntimeError):
    """Raised when challenge analysis fails."""


class ChallengeAnalysisService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Groq | None = Groq(api_key=settings.groq_api_key) if settings.groq_api_key else None

    def analyze_attempt(
        self,
        transcript: str,
        prompt_text: str,
        vocal_goal: str,
        metrics: dict,
    ) -> ChallengeEvaluation:
        if self._client is None:
            # Fallback evaluation based on simple WPM rules
            wpm = metrics.get("wpm", 130)
            fillers_count = metrics.get("filler_words_count", 0)
            
            pacing_rating = "Excellent speed"
            pacing_explanation = "At 120-150 WPM, your pace is perfect for conversational and clear speaking."
            if wpm < 110:
                pacing_rating = "Slow delivery"
                pacing_explanation = "You spoke at a slow rate. This is excellent for creating gravity and authority, but be careful not to make the audience lose energy."
            elif wpm > 160:
                pacing_rating = "Fast pacing"
                pacing_explanation = "You spoke at a fast pace. This conveys energy and enthusiasm, but try to use more pauses to allow the audience to digest the content."

            score = 100 - (fillers_count * 5)
            if wpm < 100 or wpm > 160:
                score -= 10
            score = max(50, min(100, score))
            
            strengths = ["Clear articulation of words.", "Good speech volume levels."]
            if fillers_count == 0:
                strengths.append("Fantastic control over your speech flow; zero filler words detected.")

            areas_for_improvement = []
            if fillers_count > 0:
                areas_for_improvement.append(f"You used {fillers_count} filler words. Pause instead of filling the silence.")
            if wpm < 100:
                areas_for_improvement.append("Pace is slightly slow. Vary your rate to keep listeners engaged.")
            elif wpm > 160:
                areas_for_improvement.append("Pace is slightly fast. Slow down and add a dramatic pause at key points.")
            
            if not areas_for_improvement:
                areas_for_improvement.append("Try experimenting with varying your vocal pitch (high vs. low) to build drama.")

            return ChallengeEvaluation(
                score=score,
                pacing_rating=pacing_rating,
                pacing_explanation=pacing_explanation,
                filler_words_breakdown=[FillerWordMetric(**x) for x in metrics.get("filler_words_breakdown", [])],
                strengths=strengths,
                areas_for_improvement=areas_for_improvement,
                vinh_giang_drill=(
                    "The Clap Pause Drill: Read the text and clap your hands once every time there is a punctuation mark. "
                    "This forces your brain to pause physically, replacing the filler word habits."
                )
            )

        prompt = (
            "You are Vinh Giang, a world-class public speaking and vocal presence coach. "
            "Your feedback is inspiring, highly actionable, direct, and constructive. "
            "You evaluate speakers on pacing, filler words, melody (vocal variety), and pauses. "
            "Return strict JSON with keys: "
            "score (integer 0-100), "
            "pacing_rating (short string), "
            "pacing_explanation (string), "
            "strengths (array of strings), "
            "areas_for_improvement (array of strings), "
            "vinh_giang_drill (string: a hands-on physical or vocal exercise designed to fix their main issue). "
            "Do not include the filler_words_breakdown key in the response; it is calculated separately by the service."
        )

        user_content = (
            f"User Attempt Transcript: '{transcript}'\n"
            f"Original Target Text to read: '{prompt_text}'\n"
            f"Vocal Goal of Challenge: '{vocal_goal}'\n"
            f"Calculated Metrics:\n"
            f"- Words Per Minute (WPM): {metrics['wpm']}\n"
            f"- Filler Words Count: {metrics['filler_words_count']}\n"
            f"- Total Duration: {metrics['duration_seconds']}s\n\n"
            "Analyze the attempt and provide structured coaching feedback."
        )

        try:
            response = self._client.chat.completions.create(
                model=self._settings.groq_llm_model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_content},
                ],
            )
        except Exception as exc:
            logger.exception("Groq challenge analysis request failed")
            raise ChallengeAnalysisError("Failed to analyze challenge attempt") from exc

        choices = getattr(response, "choices", None)
        if not choices:
            raise ChallengeAnalysisError("Model response did not match expected schema")

        first_choice = choices[0]
        message = getattr(first_choice, "message", None)
        raw = getattr(message, "content", None) or "{}"
        try:
            data = json.loads(raw)
            # Inject filler words breakdown calculated locally
            data["filler_words_breakdown"] = metrics.get("filler_words_breakdown", [])
            return ChallengeEvaluation.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.warning("Failed parsing model response: %s. Raw content: %s", exc, raw)
            raise ChallengeAnalysisError("Model response did not match expected schema") from exc
