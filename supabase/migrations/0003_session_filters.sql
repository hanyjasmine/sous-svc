-- keep the parsed search filters so chat can narrow or re-run the search later
alter table public.search_sessions add column filters jsonb;
