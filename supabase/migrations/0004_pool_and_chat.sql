-- where the next provider page starts, so scrolling can pull more results
alter table public.search_sessions
  add column provider_offset int not null default 0,
  add column provider_total int not null default 0;

-- pools keyed by normalized filters, reused for an hour (spoonacular terms)
create table public.search_cache (
  key text primary key,
  filters jsonb not null,
  recipe_ids uuid[] not null,
  provider_offset int not null default 0,
  provider_total int not null default 0,
  created_at timestamptz not null default now()
);
alter table public.search_cache enable row level security;
grant all on public.search_cache to service_role;

-- system messages hold the result pool the model saw that turn
alter table public.chat_messages drop constraint chat_messages_role_check;
alter table public.chat_messages
  add constraint chat_messages_role_check check (role in ('user', 'assistant', 'system'));

-- rows inserted together share created_at, seq keeps their order
alter table public.chat_messages add column seq bigint generated always as identity;
create index chat_messages_seq_idx on public.chat_messages (session_id, seq);
