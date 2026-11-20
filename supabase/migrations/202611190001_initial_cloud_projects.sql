create extension if not exists pgcrypto;

create table if not exists public.profiles (
    id uuid primary key references auth.users(id) on delete cascade,
    display_name text,
    email text,
    avatar_url text,
    created_at timestamptz not null default now(),
    last_sign_in_at timestamptz,
    storage_used_bytes bigint not null default 0 check (storage_used_bytes >= 0)
);

create table if not exists public.projects (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    name text not null check (length(trim(name)) between 1 and 160),
    source_filename text,
    duration_seconds double precision check (duration_seconds is null or duration_seconds >= 0),
    media_size_bytes bigint not null default 0 check (media_size_bytes >= 0),
    thumbnail_data text,
    state jsonb not null default '{}'::jsonb,
    media_status text not null default 'local_only'
        check (media_status in ('local_only', 'uploading', 'cloud')),
    version bigint not null default 1 check (version > 0),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    last_cloud_saved_at timestamptz not null default now(),
    unique (id, user_id)
);

create index if not exists projects_user_updated_idx
    on public.projects (user_id, updated_at desc);

create table if not exists public.project_versions (
    id uuid primary key default gen_random_uuid(),
    project_id uuid not null,
    user_id uuid not null,
    version bigint not null,
    state jsonb not null,
    created_at timestamptz not null default now(),
    foreign key (project_id, user_id)
        references public.projects(id, user_id) on delete cascade,
    unique (project_id, version)
);

create index if not exists project_versions_project_created_idx
    on public.project_versions (project_id, created_at desc);

create table if not exists public.project_assets (
    id uuid primary key default gen_random_uuid(),
    project_id uuid not null,
    user_id uuid not null,
    asset_type text not null check (asset_type in ('video', 'thumbnail', 'analysis')),
    storage_path text not null unique,
    original_filename text,
    content_type text,
    file_size_bytes bigint not null default 0 check (file_size_bytes >= 0),
    sha256 text check (sha256 is null or sha256 ~ '^[0-9a-f]{64}$'),
    upload_status text not null default 'pending'
        check (upload_status in ('pending', 'uploading', 'complete', 'failed', 'cancelled')),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    foreign key (project_id, user_id)
        references public.projects(id, user_id) on delete cascade,
    check (storage_path like user_id::text || '/' || project_id::text || '/%'),
    check (upload_status <> 'complete' or sha256 is not null)
);

create index if not exists project_assets_project_idx
    on public.project_assets (project_id, user_id);

create table if not exists public.sync_queue (
    id uuid primary key default gen_random_uuid(),
    project_id uuid,
    user_id uuid not null references auth.users(id) on delete cascade,
    operation text not null check (operation in ('upsert', 'delete', 'upload_asset')),
    payload jsonb not null default '{}'::jsonb,
    status text not null default 'pending'
        check (status in ('pending', 'completed', 'conflict', 'failed')),
    error_message text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    foreign key (project_id, user_id)
        references public.projects(id, user_id) on delete cascade
);

create index if not exists sync_queue_user_pending_idx
    on public.sync_queue (user_id, created_at)
    where status = 'pending';

create or replace function public.sync_profile_from_auth()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    insert into public.profiles (id, display_name, email, avatar_url, last_sign_in_at)
    values (
        new.id,
        coalesce(new.raw_user_meta_data ->> 'full_name', new.raw_user_meta_data ->> 'name'),
        new.email,
        coalesce(new.raw_user_meta_data ->> 'avatar_url', new.raw_user_meta_data ->> 'picture'),
        new.last_sign_in_at
    )
    on conflict (id) do update set
        display_name = excluded.display_name,
        email = excluded.email,
        avatar_url = excluded.avatar_url,
        last_sign_in_at = excluded.last_sign_in_at;
    return new;
end;
$$;

drop trigger if exists on_auth_user_profile_sync on auth.users;
create trigger on_auth_user_profile_sync
after insert or update of email, raw_user_meta_data, last_sign_in_at on auth.users
for each row execute function public.sync_profile_from_auth();

create or replace function public.bump_project_version()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if new.user_id <> old.user_id then
        raise exception 'A project owner cannot be changed';
    end if;
    if new.name is distinct from old.name
       or new.source_filename is distinct from old.source_filename
       or new.duration_seconds is distinct from old.duration_seconds
       or new.media_size_bytes is distinct from old.media_size_bytes
       or new.thumbnail_data is distinct from old.thumbnail_data
       or new.state is distinct from old.state then
        new.version := old.version + 1;
    else
        new.version := old.version;
    end if;
    new.updated_at := now();
    new.last_cloud_saved_at := now();
    return new;
end;
$$;

drop trigger if exists projects_bump_version on public.projects;
create trigger projects_bump_version
before update on public.projects
for each row execute function public.bump_project_version();

create or replace function public.snapshot_project_version()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    if old.state is distinct from new.state
       or old.name is distinct from new.name
       or old.source_filename is distinct from new.source_filename
       or old.duration_seconds is distinct from new.duration_seconds
       or old.media_size_bytes is distinct from new.media_size_bytes
       or old.thumbnail_data is distinct from new.thumbnail_data then
        insert into public.project_versions (project_id, user_id, version, state)
        values (old.id, old.user_id, old.version, old.state)
        on conflict (project_id, version) do nothing;

        delete from public.project_versions
        where id in (
            select id
            from public.project_versions
            where project_id = old.id and user_id = old.user_id
            order by version desc
            offset 20
        );
    end if;
    return new;
end;
$$;

drop trigger if exists projects_snapshot_version on public.projects;
create trigger projects_snapshot_version
after update on public.projects
for each row execute function public.snapshot_project_version();

create or replace function public.refresh_profile_storage_usage()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    affected_user uuid;
begin
    affected_user := coalesce(new.user_id, old.user_id);
    update public.profiles
    set storage_used_bytes = (
        select coalesce(sum(file_size_bytes), 0)
        from public.project_assets
        where user_id = affected_user and upload_status = 'complete'
    )
    where id = affected_user;
    if tg_op = 'DELETE' then
        return old;
    end if;
    return new;
end;
$$;

drop trigger if exists project_assets_refresh_storage_usage on public.project_assets;
create trigger project_assets_refresh_storage_usage
after insert or update or delete on public.project_assets
for each row execute function public.refresh_profile_storage_usage();

alter table public.profiles enable row level security;
alter table public.projects enable row level security;
alter table public.project_versions enable row level security;
alter table public.project_assets enable row level security;
alter table public.sync_queue enable row level security;

drop policy if exists "profiles_select_own" on public.profiles;
create policy "profiles_select_own" on public.profiles
for select to authenticated using (id = (select auth.uid()));

drop policy if exists "projects_owner_all" on public.projects;
create policy "projects_owner_all" on public.projects
for all to authenticated using (user_id = (select auth.uid()))
with check (user_id = (select auth.uid()));

drop policy if exists "project_versions_owner_all" on public.project_versions;
create policy "project_versions_owner_all" on public.project_versions
for all to authenticated using (user_id = (select auth.uid()))
with check (user_id = (select auth.uid()));

drop policy if exists "project_assets_owner_all" on public.project_assets;
create policy "project_assets_owner_all" on public.project_assets
for all to authenticated using (user_id = (select auth.uid()))
with check (user_id = (select auth.uid()));

drop policy if exists "sync_queue_owner_all" on public.sync_queue;
create policy "sync_queue_owner_all" on public.sync_queue
for all to authenticated using (user_id = (select auth.uid()))
with check (user_id = (select auth.uid()));

insert into storage.buckets (id, name, public)
values ('project-media', 'project-media', false)
on conflict (id) do update set public = false;

drop policy if exists "project_media_owner_select" on storage.objects;
create policy "project_media_owner_select" on storage.objects
for select to authenticated using (
    bucket_id = 'project-media'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and exists (
        select 1 from public.project_assets asset
        where asset.storage_path = storage.objects.name
          and asset.user_id = (select auth.uid())
    )
);

drop policy if exists "project_media_owner_insert" on storage.objects;
create policy "project_media_owner_insert" on storage.objects
for insert to authenticated with check (
    bucket_id = 'project-media'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and exists (
        select 1 from public.project_assets asset
        where asset.storage_path = storage.objects.name
          and asset.user_id = (select auth.uid())
    )
);

drop policy if exists "project_media_owner_update" on storage.objects;
create policy "project_media_owner_update" on storage.objects
for update to authenticated using (
    bucket_id = 'project-media'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and exists (
        select 1 from public.project_assets asset
        where asset.storage_path = storage.objects.name
          and asset.user_id = (select auth.uid())
    )
) with check (
    bucket_id = 'project-media'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and exists (
        select 1 from public.project_assets asset
        where asset.storage_path = storage.objects.name
          and asset.user_id = (select auth.uid())
    )
);

drop policy if exists "project_media_owner_delete" on storage.objects;
create policy "project_media_owner_delete" on storage.objects
for delete to authenticated using (
    bucket_id = 'project-media'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and exists (
        select 1 from public.project_assets asset
        where asset.storage_path = storage.objects.name
          and asset.user_id = (select auth.uid())
    )
);
