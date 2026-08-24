-- Journal contents encrypted at rest (application-layer AES-256-GCM).
--
-- Sensitive free-text fields (transcript, title, summary, takeaway, mood,
-- mood_explanation, themes, insights) move into a single data_encrypted blob
-- managed by app/core/crypto.py. Non-sensitive columns used for SQL filtering
-- and ordering stay as real columns: id, user_id, recording_session_id,
-- audio_path, prompt_version, recorded_at, created_at.
--
-- Old plaintext columns are kept until a follow-up migration drops them after
-- scripts/backfill_encrypt.py has encrypted all existing rows.

alter table public.journals
    add column if not exists data_encrypted text,
    add column if not exists enc_version integer not null default 1;

create index if not exists journals_enc_backfill_idx
    on public.journals (user_id)
    where data_encrypted is null;
