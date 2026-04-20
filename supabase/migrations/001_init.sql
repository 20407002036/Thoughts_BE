-- Week 2 baseline schema for Mindful Moments backend

create extension if not exists pgcrypto;

create table if not exists public.user_profiles (
    id uuid primary key references auth.users(id) on delete cascade,
    display_name text,
    streak_count integer not null default 0,
    last_journal_saved timestamptz,
    created_at timestamptz not null default timezone('utc', now()),
    updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.journals (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    transcript text not null,
    mood text not null,
    title text not null,
    summary text not null,
    themes text[] not null default '{}',
    insights text[] not null default '{}',
    audio_path text not null,
    audio_signed_url text,
    prompt_version text not null,
    created_at timestamptz not null default timezone('utc', now())
);

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
