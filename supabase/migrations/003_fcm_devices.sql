-- Firebase Cloud Messaging device registry.
--
-- One row per registered device token. `token` is globally unique because
-- FCM rotates tokens on app reinstall / restore: when a token already exists
-- under a different user (shared family device, or a user who re-registers
-- after signing out) the API reassigns it rather than storing a duplicate,
-- otherwise the old account would keep receiving push.
--
-- Notification content is never stored here. `notification_log` is an audit
-- trail of what was sent, used for per-day dedupe of event-driven pushes and
-- for debugging delivery; the push body itself lives in the journal row.

create table if not exists public.device_tokens (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    token text not null unique,
    platform text not null default 'android' check (platform in ('android', 'ios', 'web')),
    app_version text,
    active boolean not null default true,
    created_at timestamptz not null default timezone('utc', now()),
    last_seen_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.notification_log (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    kind text not null,
    entry_id uuid,
    sent_at timestamptz not null default timezone('utc', now())
);

-- Partial index: the only query the sender makes is "active tokens for user".
-- Reassignment deactivates the old row rather than deleting it, so this index
-- stays small even as users churn devices.
create index if not exists device_tokens_user_active_idx
    on public.device_tokens (user_id)
    where active;

-- Supports the "has this user already been notified about this today?"
-- dedupe check and the support-flavoured lookups by user + recency.
create index if not exists notification_log_user_kind_sent_at_idx
    on public.notification_log (user_id, kind, sent_at desc);

alter table public.device_tokens enable row level security;
alter table public.notification_log enable row level security;

-- The backend talks to these tables with the service role key, which bypasses
-- RLS. These policies exist so a client using the anon key (e.g. a direct
-- PostgREST call from a future web build) can only ever see its own rows.
create policy "Users can view own devices"
    on public.device_tokens
    for select
    using (auth.uid() = user_id);

create policy "Users can register own devices"
    on public.device_tokens
    for insert
    with check (auth.uid() = user_id);

create policy "Users can update own devices"
    on public.device_tokens
    for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

create policy "Users can delete own devices"
    on public.device_tokens
    for delete
    using (auth.uid() = user_id);

create policy "Users can view own notification log"
    on public.notification_log
    for select
    using (auth.uid() = user_id);
