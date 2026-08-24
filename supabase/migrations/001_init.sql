-- Week 2 baseline schema for Mindful Moments backend

create extension if not exists pgcrypto;

create table if not exists public.user_profiles (
    id uuid primary key references auth.users(id) on delete cascade,
    display_name text,
    avatar_url text,
    timezone text not null default 'UTC',
    tagline text,
    streak_count integer not null default 0,
    voice_minutes integer not null default 0,
    milestones text[] not null default '{}',
    next_milestone text,
    last_journal_saved timestamptz,
    notifications_enabled boolean not null default true,
    prompt_reminder_time text,
    appearance_mode text not null default 'system',
    audio_quality text not null default 'standard',
    language text not null default 'en',
    encryption_status text not null default 'managed',
    created_at timestamptz not null default timezone('utc', now()),
    updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.journals (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    recording_session_id uuid,
    transcript text not null,
    mood text not null,
    mood_explanation text,
    title text not null,
    summary text not null,
    takeaway text,
    themes text[] not null default '{}',
    insights text[] not null default '{}',
    audio_path text not null,
    audio_signed_url text,
    prompt_version text not null,
    recorded_at timestamptz,
    created_at timestamptz not null default timezone('utc', now())
);

alter table public.user_profiles
    add column if not exists avatar_url text,
    add column if not exists timezone text not null default 'UTC',
    add column if not exists tagline text,
    add column if not exists voice_minutes integer not null default 0,
    add column if not exists milestones text[] not null default '{}',
    add column if not exists next_milestone text,
    add column if not exists notifications_enabled boolean not null default true,
    add column if not exists prompt_reminder_time text,
    add column if not exists appearance_mode text not null default 'system',
    add column if not exists audio_quality text not null default 'standard',
    add column if not exists language text not null default 'en',
    add column if not exists encryption_status text not null default 'managed';

alter table public.journals
    add column if not exists recording_session_id uuid,
    add column if not exists mood_explanation text,
    add column if not exists takeaway text,
    add column if not exists recorded_at timestamptz;

create index if not exists journals_user_id_created_at_idx
    on public.journals (user_id, created_at desc);

alter table public.user_profiles enable row level security;
alter table public.journals enable row level security;

create policy "Users can view own profile"
    on public.user_profiles
    for select
    using (auth.uid() = id);

create policy "Users can update own profile"
    on public.user_profiles
    for update
    using (auth.uid() = id)
    with check (auth.uid() = id);

create policy "Users can view own journals"
    on public.journals
    for select
    using (auth.uid() = user_id);

create policy "Users can insert own journals"
    on public.journals
    for insert
    with check (auth.uid() = user_id);

create policy "Users can update own journals"
    on public.journals
    for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

create policy "Users can delete own journals"
    on public.journals
    for delete
    using (auth.uid() = user_id);

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    insert into public.user_profiles (id)
    values (new.id)
    on conflict (id) do nothing;
    return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row execute procedure public.handle_new_user();
