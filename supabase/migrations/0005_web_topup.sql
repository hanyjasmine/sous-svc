-- web results fill in a thin pool in the background, the frontend polls this
alter table public.search_sessions
  add column web_status text check (web_status in ('pending', 'done')),
  add column web_started_at timestamptz;
