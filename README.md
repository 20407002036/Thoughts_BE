# Mindful Moments Backend

FastAPI backend for audio journal ingestion, transcription, analysis, and journal persistence.

## Purpose

This service accepts journal audio uploads and runs a full pipeline:

1. Validate request and file constraints.
2. Store audio (Supabase Storage or local fallback).
3. Transcribe audio (Groq Whisper or local fallback text).
4. Analyze transcript into structured JSON (Groq LLM or local fallback analysis).
5. Persist final journal entry (Supabase table or local in-memory fallback).

## Local setup

1. Create and activate a Python virtual environment.
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Copy environment template:

   ```bash
   cp .env.example .env
   ```

4. Start the API:

   ```bash
   uvicorn app.main:app --reload
   ```

## Render deployment

- Build Command: `bash render_build_setup.sh`
- Start Command: `bash render_start.sh`
- Runtime: `runtime.txt` pins Python 3.13.0 so `pydantic-core` and PyO3 stay compatible.

## Environment variables

| Variable | Required | Description |
| --- | --- | --- |
| APP_NAME | no | Display name for API metadata |
| APP_ENV | no | Runtime environment name |
| APP_HOST | no | Bind host |
| APP_PORT | no | Bind port |
| LOG_LEVEL | no | Logging level |
| AUTH_REQUIRED | no | If false, auth uses a local dev user |
| SUPABASE_URL | prod | Supabase project URL |
| SUPABASE_ANON_KEY | optional | Required for server-side /v1/auth/* endpoints |
| SUPABASE_SERVICE_ROLE_KEY | prod | Service role for DB/storage operations |
| SUPABASE_JWT_SECRET | optional | Required only for legacy Supabase projects issuing HS256 access tokens |
| SUPABASE_JWT_AUDIENCE | no | JWT audience validation target |
| SUPABASE_BUCKET | no | Storage bucket for audio uploads |
| SUPABASE_JOURNALS_TABLE | no | Journal table name |
| SUPABASE_PROFILES_TABLE | no | Profile table name |
| SIGNED_URL_EXPIRY_SECONDS | no | Signed URL TTL |
| GROQ_API_KEY | prod | Groq API key |
| GROQ_WHISPER_MODEL | no | Whisper model name |
| GROQ_LLM_MODEL | no | Analysis model name |
| MAX_UPLOAD_MB | no | Max upload size guardrail |
| REQUEST_TIMEOUT_SECONDS | no | Request and pipeline stage timeout |
| ANALYSIS_PROMPT_VERSION | no | Version marker stored per journal |
| FIREBASE_ENABLED | no | Master switch for FCM push. `false` makes every push a no-op |
| FIREBASE_PROJECT_ID | prod (if FCM on) | Firebase project the service account belongs to |
| FIREBASE_CREDENTIALS_PATH | one of | Path to a service account JSON file |
| FIREBASE_CREDENTIALS_JSON | one of | Same JSON inline, for platforms with no filesystem secret |
| FIREBASE_DRY_RUN | no | Validate without delivering. Keep true in staging |
| FIREBASE_DEVICES_TABLE | no | Device token table name |
| FIREBASE_NOTIFICATION_LOG_TABLE | no | Notification audit table name |
| FCM_BATCH_SIZE | no | Tokens per multicast call. FCM caps this at 500 |

Notes:

- Local fallback mode works without Supabase and Groq credentials.
- For production-like behavior, set all Supabase and Groq values.
- With no Firebase credentials the Admin SDK falls back to Application Default
  Credentials, which is the right choice when the runtime already has the
  service account attached.

## API contracts

### GET /health

Liveness endpoint.

Example response:

```json
{
  "status": "ok"
}
```

### GET /ready

Readiness and configuration summary.

Example response:

```json
{
  "status": "ready",
  "environment": "development",
  "auth_required": true,
  "supabase_configured": false,
  "groq_configured": false,
  "firebase_configured": false
}
```

### POST /v1/journals/ingest

Authenticated multipart audio ingestion endpoint.

Request:

- Content-Type: multipart/form-data
- Field: audio (required UploadFile, must be audio/*)
- Header: Authorization: Bearer <access-token> when AUTH_REQUIRED=true
- Optional header: X-Correlation-ID

Success response shape:

```json
{
  "id": "entry-uuid",
  "user_id": "user-uuid",
  "transcript": "Today I felt focused.",
  "analysis": {
    "mood": "calm",
    "title": "Steady Day",
    "summary": "A calm and focused day.",
    "themes": ["balance"],
    "insights": ["Breathing helped reset focus."]
  },
  "audio_path": "user-uuid/asset.mp3",
  "audio_signed_url": "https://...",
  "prompt_version": "v1",
  "created_at": "2026-04-15T00:00:00Z"
}
```

### GET /v1/entries

Returns paginated journal summaries. `/v1/journals` is also supported as an alias.

Query parameters:

- `limit` 1-100, default 20
- `offset` default 0
- `month` optional `YYYY-MM`
- `query` optional text search
- `tag` optional theme/tag filter

Success response shape:

```json
{
  "entries": [
    {
      "id": "entry-uuid",
      "entry_id": "entry-uuid",
      "title": "Steady Day",
      "created_at": "2026-05-04T08:00:00Z",
      "summary": "A calm and focused day.",
      "status": "completed",
      "mood_label": "calm"
    }
  ],
  "limit": 20,
  "offset": 0,
  "total": 1
}
```

### GET /v1/entries/{entryId}

Returns journal detail. `/v1/journals/{entryId}` is also supported as an alias.

Success response shape:

```json
{
  "id": "entry-uuid",
  "recording_session_id": null,
  "title": "Steady Day",
  "created_at": "2026-05-04T08:00:00Z",
  "recorded_at": "2026-05-04T08:00:00Z",
  "transcript": {
    "full_text": "Today I felt focused."
  },
  "tags": [
    {
      "label": "balance",
      "source": "analysis"
    }
  ],
  "mood_analysis": {
    "label": "calm",
    "score": null,
    "confidence": null,
    "explanation": null
  },
  "takeaway": "A calm and focused day.",
  "summary": "A calm and focused day.",
  "highlights": ["Breathing helped reset focus."],
  "audio_path": "user-uuid/asset.mp3",
  "audio_signed_url": "https://...",
  "prompt_version": "v1"
}
```

### PATCH /v1/entries/{entryId}

Updates editable journal fields. `/v1/journals/{entryId}` is also supported as an alias.

Request body:

```json
{
  "title": "Updated title",
  "summary": "Updated summary",
  "tags": ["calm", "reflection"]
}
```

### POST /v1/entries/{entryId}/export

Export is not supported in this phase. The endpoint returns `501 unsupported` with the standard error envelope so clients can disable export cleanly.

### POST /v1/entries/{entryId}/share

Share-link generation is not supported in this phase. The endpoint returns `501 unsupported` with the standard error envelope so clients can disable sharing cleanly.

### POST /v1/recordings

Creates a recording upload using the current synchronous ingest pipeline. Because processing completes in the request for this MVP, successful responses return `completed` with `progress_percent: 100`.

Request:

- Content-Type: multipart/form-data
- Field: audio (required UploadFile, must be audio/*)

Success response shape:

```json
{
  "recording_id": "entry-uuid",
  "status": "completed",
  "progress_percent": 100,
  "error_message": null,
  "entry_id": "entry-uuid",
  "draft_id": null
}
```

### GET /v1/recordings/{recordingId}

Returns recording state. In the current synchronous implementation, existing recordings resolve to `completed` and use the journal entry id as the recording id.

### POST /v1/auth/login

Supabase email/password sign-in endpoint.

Request body:

```json
{
  "email": "user@example.com",
  "password": "secret123"
}
```

Success response shape:

```json
{
  "access_token": "eyJhbGciOi...",
  "token_type": "bearer",
  "expires_in": 3600,
  "refresh_token": "...",
  "user_id": "user-uuid",
  "email": "user@example.com"
}
```

### POST /v1/auth/signup

Supabase email/password signup endpoint.

Request body:

```json
{
  "email": "new-user@example.com",
  "password": "secret123"
}
```

Success response shape:

```json
{
  "user_id": "user-uuid",
  "email": "new-user@example.com",
  "email_confirmed": false,
  "access_token": null,
  "token_type": null,
  "expires_in": null,
  "refresh_token": null
}
```

When email confirmation is disabled in Supabase auth settings, signup may return non-null access and refresh tokens.

### POST /v1/auth/refresh

Supabase refresh-token exchange endpoint.

Request body:

```json
{
  "refresh_token": "refresh-token-value"
}
```

Success response shape:

```json
{
  "access_token": "eyJhbGciOi...",
  "token_type": "bearer",
  "expires_in": 3600,
  "refresh_token": "new-refresh-token",
  "user_id": "user-uuid",
  "email": "user@example.com"
}
```

### POST /v1/auth/logout

Supabase session logout endpoint.

Request:

- Header: Authorization: Bearer <access-token>

Success response shape:

```json
{
  "success": true
}
```

### GET /v1/profile

Returns the current authenticated user's profile.

Request:

- Header: Authorization: Bearer <access-token> when AUTH_REQUIRED=true

Success response shape:

```json
{
  "user_id": "user-uuid",
  "email": "user@example.com",
  "display_name": "Calm Mind",
  "streak_count": 2,
  "last_journal_saved": "2026-04-21T00:00:00Z",
  "created_at": "2026-04-20T00:00:00Z",
  "updated_at": "2026-04-21T00:00:00Z"
}
```

### PATCH /v1/profile

Updates the current authenticated user's profile display name.

Request:

- Header: Authorization: Bearer <access-token> when AUTH_REQUIRED=true
- Content-Type: application/json

Request body:

```json
{
  "display_name": "New Display Name"
}
```

Success response shape:

```json
{
  "user_id": "user-uuid",
  "email": "user@example.com",
  "display_name": "New Display Name",
  "streak_count": 2,
  "last_journal_saved": "2026-04-21T00:00:00Z",
  "created_at": "2026-04-20T00:00:00Z",
  "updated_at": "2026-04-21T00:00:00Z"
}
```

### GET /v1/preferences

Returns user preferences.

Success response shape:

```json
{
  "notifications_enabled": true,
  "prompt_reminder_time": null,
  "appearance_mode": "system",
  "audio_quality": "standard",
  "language": "en",
  "encryption_status": "managed"
}
```

### PATCH /v1/preferences

Updates user preferences.

Request body:

```json
{
  "notifications_enabled": false,
  "prompt_reminder_time": "08:30",
  "appearance_mode": "dark",
  "audio_quality": "high",
  "language": "en"
}
```

### GET /v1/dashboard

Returns dashboard summary data. Prompt generation is not implemented yet, so the response deliberately returns `prompt: null` and `prompt_status: unavailable` instead of placeholder copy.
Success response shape:

```json
{
  "prompt": null,
  "prompt_status": "unavailable",
  "recent_entries": [],
  "streak_count": 0,
  "entry_count": 0
}
```

### POST /v1/devices

Registers or refreshes this device's FCM registration token. Authenticated.

Request:

```json
{
  "token": "fcm-registration-token",
  "platform": "android",
  "app_version": "1.4.0"
}
```

- `token` — required, 8–512 characters
- `platform` — optional, one of `android`, `ios`, `web`. Defaults to `android`
- `app_version` — optional, up to 32 characters

Success response (`201`):

```json
{
  "device_id": "0f6c1f2e-1f1e-4a9f-9d5f-2c7a1b8e4d31",
  "platform": "android",
  "registered": true
}
```

Idempotent: re-registering the same token returns the same `device_id` and
updates `platform` / `app_version`. A token previously registered to a
different account is reassigned to the caller.

### DELETE /v1/devices/{token}

Stops push to this device. Scoped to the calling user, so one account cannot
unregister another's device. Idempotent — an unknown token returns `200` with
`removed: false` rather than `404`, because sign-out runs on clients that may
never have registered.

```json
{
  "success": true,
  "removed": true
}
```

### POST /v1/devices/test

Sends a fixed diagnostic notification to the caller's own devices so the client
can confirm end-to-end delivery without waiting for a real event. Rate limited
to 3 calls per minute. The body is static and never carries journal content.

```json
{
  "status": "sent",
  "sent": 2,
  "failed": 0,
  "reason": null
}
```

`status` is `sent`, `skipped`, or `failed`. `reason` explains a skip —
`firebase_not_configured`, `notifications_disabled`, `no_active_devices`,
`preference_check_failed` — or a `failed` status, where it is
`token_lookup_failed` or `messaging_unavailable`.

Error envelope for all handled failures:

```json
{
  "error": "validation_error",
  "message": "Request validation failed",
  "correlation_id": "2cb5c6f8-3f15-4ece-a565-bfef4e4fb019"
}
```
Common error codes:

- 400 bad_request
- 401 unauthorized
- 413 payload_too_large
- 422 validation_error
- 502 upstream_error
- 504 request_timeout
- 501 unsupported
- 500 internal_error

## Push notifications (FCM)

Event-driven push over Firebase Cloud Messaging. There are no scheduled or
recurring notifications: a push is only sent in response to something that
already happened in the product.

### What is sent

One notification type today — `journal_ready`, dispatched after the ingest
pipeline persists an entry:

```json
{
  "notification": { "title": "Your journal is ready", "body": "Tap to read what you just recorded." },
  "data": { "type": "journal_ready", "entry_id": "<entry-uuid>" }
}
```

Notification bodies are fixed strings. Journal text is encrypted at rest and
never leaves the database, so the client receives only the entry id in `data`
and fetches the entry over the authenticated API on tap. Android and iOS are
served by the same multicast: the message carries both an `AndroidConfig`
(`priority: high`) and an `APNSConfig` (`apns-priority: 10`), and each
registration records which platform it belongs to.

### Opting in and out

`notifications_enabled` in `user_profiles` is the single switch. It is
read through the existing preferences repository, so `PATCH /v1/preferences`
with `{"notifications_enabled": false}` stops all delivery with no change to
device registrations.

### Token lifecycle

`POST /v1/devices` is idempotent and must be called on every app launch and
again whenever the FCM SDK hands back a refreshed token. A token is unique
across the whole table rather than unique per user: when a device changes
accounts, re-registering reassigns the token and the previous account stops
receiving push on that device.

A token is deactivated when FCM reports it as permanently unusable
(`UnregisteredError` — the app was uninstalled — or `SenderIdMismatchError` —
the token belongs to another Firebase project). Server-side failures such as
quota exhaustion, APNs certificate problems, or a 404 from the FCM endpoint
deliberately **do not** deactivate tokens; pruning on those would silently
disable push for every user during an outage.

### Which path sends

| Ingest mode | Behaviour |
| --- | --- |
| `INGEST_ASYNC=false` (local dev) | `PushService` is called directly from the pipeline |
| `INGEST_ASYNC=true` (production) | Pipeline enqueues the `worker.send_push` Celery task |

Either way the push stage is best-effort and bounded by
`REQUEST_TIMEOUT_SECONDS`: a Firebase outage can never turn a saved journal
into a failed ingest, because the entry is already persisted before the push
stage runs.

### Client integration

1. Request notification permission.
2. On token receipt or refresh, `POST /v1/devices` with the token and platform.
3. On sign-out or when the user revokes permission, `DELETE /v1/devices/{token}`.
4. On receiving a push, read `data.type`; for `journal_ready`, open
   `GET /v1/entries/{data.entry_id}`.

## cURL examples
Health check:

```bash
curl -s http://127.0.0.1:8000/health
```

Readiness check:

```bash
curl -s http://127.0.0.1:8000/ready
```

Ingest audio with auth:

```bash
curl -s -X POST "http://127.0.0.1:8000/v1/journals/ingest" \
  -H "Authorization: Bearer <supabase-access-token>" \
  -H "X-Correlation-ID: demo-corr-id" \
  -F "audio=@./samples/journal.mp3;type=audio/mpeg"
```

Sign in and get a token:

```bash
curl -s -X POST "http://127.0.0.1:8000/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"secret123"}'
```

Sign up a user:

```bash
curl -s -X POST "http://127.0.0.1:8000/v1/auth/signup" \
  -H "Content-Type: application/json" \
  -d '{"email":"new-user@example.com","password":"secret123"}'
```

Refresh session token:

```bash
curl -s -X POST "http://127.0.0.1:8000/v1/auth/refresh" \
  -H "Content-Type: application/json" \
  -d '{"refresh_token":"<supabase-refresh-token>"}'
```

Logout session:

```bash
curl -s -X POST "http://127.0.0.1:8000/v1/auth/logout" \
  -H "Authorization: Bearer <supabase-access-token>"
```

Get current profile:

```bash
curl -s "http://127.0.0.1:8000/v1/profile" \
  -H "Authorization: Bearer <supabase-access-token>"
```

Update display name:

```bash
curl -s -X PATCH "http://127.0.0.1:8000/v1/profile" \
  -H "Authorization: Bearer <supabase-access-token>" \
  -H "Content-Type: application/json" \
  -d '{"display_name":"New Display Name"}'
```

Ingest audio in local-dev mode (AUTH_REQUIRED=false):

```bash
curl -s -X POST "http://127.0.0.1:8000/v1/journals/ingest" \
  -F "audio=@./samples/journal.mp3;type=audio/mpeg"
```

Register a device for push (no Firebase configuration needed — registration is
stored regardless of whether delivery is enabled):

```bash
curl -s -X POST "http://127.0.0.1:8000/v1/devices" \
  -H "Authorization: Bearer <supabase-access-token>" \
  -H "Content-Type: application/json" \
  -d '{"token":"fcm-registration-token","platform":"android","app_version":"1.4.0"}'
```

Send a test push to your own devices:

```bash
curl -s -X POST "http://127.0.0.1:8000/v1/devices/test" \
  -H "Authorization: Bearer <supabase-access-token>"
```

## Postman usage

Import collection:

- docs/postman/Mindful_Moments_MVP.postman_collection.json

Collection includes:

- GET /health
- GET /ready
- POST /v1/auth/login
- POST /v1/auth/signup
- POST /v1/auth/refresh
- POST /v1/auth/logout
- POST /v1/journals/ingest

Set collection variables in Postman:

- baseUrl (default http://127.0.0.1:8000)
- bearerToken (required only when AUTH_REQUIRED=true)

## Supabase setup

1. Fill values in .env:

   - SUPABASE_URL
   - SUPABASE_SERVICE_ROLE_KEY
   - SUPABASE_BUCKET
   - SUPABASE_JOURNALS_TABLE

2. Apply SQL migration:

   - supabase/migrations/001_init.sql
   - supabase/migrations/002_journal_encryption.sql
   - supabase/migrations/003_fcm_devices.sql (push notifications only)

3. Create the storage bucket configured by SUPABASE_BUCKET.

4. Verify authenticated flow by calling POST /v1/journals/ingest with a valid Supabase access token.

## Firebase setup

Push is off until `FIREBASE_ENABLED=true`, so this can be done after the first
deploy. 003_fcm_devices.sql must be applied either way, since registrations are
stored even while delivery is disabled.

1. Create a Firebase project and add an Android app (package name must match
   `applicationId`) and an iOS app (bundle id must match). For iOS, upload the
   APNs key and enable the Push Notifications capability — without it iOS
   deliveries will fail with an APNs auth error even though the request itself
   is valid.

2. Download a service account JSON (Project settings → Service accounts) and
   point the backend at it with either `FIREBASE_CREDENTIALS_PATH` (a file on
   disk) or `FIREBASE_CREDENTIALS_JSON` (the raw JSON, usually easier on
   Render). With neither set, the Admin SDK uses Application Default
   Credentials.

3. Set `FIREBASE_ENABLED=true` and `FIREBASE_PROJECT_ID`. Leave
   `FIREBASE_DRY_RUN=true` while testing so real devices are not disturbed, and
   confirm delivery with `POST /v1/devices/test`.

4. Check `GET /ready` reports `firebase_configured: true`.

## Verification

Run test suite:

```bash
pytest -q
```

Week 6 end-to-end verification test:

- tests/test_week6_documentation_handoff.py

Push notification tests:

- tests/test_devices_api.py — registration, idempotency, token reassignment
- tests/test_push_service.py — opt-out gating, batching, dead-token pruning
- tests/test_push_dispatch.py — pipeline stage and the `worker.send_push` task

## Handoff notes

- Correlation ID is always returned as X-Correlation-ID and in error payloads.
- Upload size guardrail is enforced at middleware and pipeline layers.
- Local fallback mode allows demoing API behavior without external providers.
