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
    streak_count: int = 0
    last_journal_saved: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class UpdateProfileRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)
