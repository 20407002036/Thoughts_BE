import re

class SpeechAnalytics:
    FILLER_WORDS = {"um", "uh", "ah", "like", "so", "you know", "er"}

    @staticmethod
    def calculate_wpm(word_count: int, duration_seconds: float) -> int:
        if duration_seconds <= 0:
            return 0
        return int((word_count / duration_seconds) * 60)

    @classmethod
    def analyze_filler_words(cls, transcript: str) -> tuple[int, list[dict]]:
        counts = {}
        # Normalize and find all alphanumeric words
        words = re.findall(r"\b[a-zA-Z\']+\b", transcript.lower())
        
        # Check simple word fillers
        for w in words:
            if w in cls.FILLER_WORDS:
                counts[w] = counts.get(w, 0) + 1
                
        # Check multi-word fillers like "you know"
        text_lower = transcript.lower()
        yk_count = len(re.findall(r"\byou know\b", text_lower))
        if yk_count:
            counts["you know"] = yk_count
            
        breakdown = [{"word": k, "count": v} for k, v in counts.items()]
        total_fillers = sum(counts.values())
        return total_fillers, breakdown
