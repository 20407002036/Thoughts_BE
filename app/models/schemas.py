from datetime import datetime

from pydantic import BaseModel, Field


class JournalAnalysis(BaseModel):
    mood: str
    title: str
    summary: str
    themes: list[str] = Field(default_factory=list)
    insights: list[str] = Field(default_factory=list)


class JournalEntryResponse(BaseModel):
    id: str
    user_id: str
    transcript: str
    analysis: JournalAnalysis
    audio_path: str
    audio_signed_url: str | None = None
    prompt_version: str
    created_at: datetime


class JournalTag(BaseModel):
    label: str
    source: str = "analysis"


class MoodAnalysis(BaseModel):
    label: str
    score: float | None = None
    confidence: float | None = None
    explanation: str | None = None


class Transcript(BaseModel):
    full_text: str


class JournalEntrySummary(BaseModel):
    id: str
    entry_id: str
    title: str
    created_at: datetime
    summary: str
    status: str = "completed"
    mood_label: str | None = None


class JournalEntryDetail(BaseModel):
    id: str
    recording_session_id: str | None = None
    title: str
    created_at: datetime
    recorded_at: datetime | None = None
    transcript: Transcript
    tags: list[JournalTag] = Field(default_factory=list)
    mood_analysis: MoodAnalysis
    takeaway: str | None = None
    summary: str | None = None
    highlights: list[str] = Field(default_factory=list)
    audio_path: str | None = None
    audio_signed_url: str | None = None
    prompt_version: str | None = None


class JournalEntryListResponse(BaseModel):
    entries: list[JournalEntrySummary]
    limit: int
    offset: int
    total: int


class UpdateJournalEntryRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    summary: str | None = Field(default=None, max_length=1000)
    tags: list[str] | None = None


class UnsupportedActionResponse(BaseModel):
    supported: bool = False
    message: str


class RecordingSessionResponse(BaseModel):
    recording_id: str
    status: str
    progress_percent: int
    error_message: str | None = None
    entry_id: str | None = None
    draft_id: str | None = None


class ErrorResponse(BaseModel):
    error: str
    message: str
    correlation_id: str


class SignInRequest(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=6)


class SignInResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int | None = None
    refresh_token: str | None = None
    user_id: str | None = None
    email: str | None = None


class SignUpRequest(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=6)


class SignUpResponse(BaseModel):
    user_id: str | None = None
    email: str | None = None
    email_confirmed: bool = False
    access_token: str | None = None
    token_type: str | None = None
    expires_in: int | None = None
    refresh_token: str | None = None


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class RefreshTokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int | None = None
    refresh_token: str | None = None
    user_id: str | None = None
    email: str | None = None


class LogoutResponse(BaseModel):
    success: bool


class ProfileResponse(BaseModel):
    user_id: str
    email: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None
    initials: str | None = None
    timezone: str | None = None
    tagline: str | None = None
    streak_count: int = 0
    entry_count: int = 0
    voice_minutes: int = 0
    milestones: list[str] = Field(default_factory=list)
    next_milestone: str | None = None
    last_journal_saved: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class UpdateProfileRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)


class UserPreferences(BaseModel):
    notifications_enabled: bool = True
    prompt_reminder_time: str | None = None
    appearance_mode: str = "system"
    audio_quality: str = "standard"
    language: str = "en"
    encryption_status: str = "managed"


class UpdatePreferencesRequest(BaseModel):
    notifications_enabled: bool | None = None
    prompt_reminder_time: str | None = None
    appearance_mode: str | None = None
    audio_quality: str | None = None
    language: str | None = None


class Prompt(BaseModel):
    id: str
    title: str
    body: str
    variant: str | None = None


class DashboardSummary(BaseModel):
    prompt: Prompt | None = None
    prompt_status: str = "unavailable"
    recent_entries: list[JournalEntrySummary] = Field(default_factory=list)
    streak_count: int = 0
    entry_count: int = 0
