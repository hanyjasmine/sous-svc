-- new supabase projects don't expose tables to the data api until granted
-- rls policies from 0001 still decide which rows each role can see

grant usage on schema public to anon, authenticated, service_role;

-- sous-svc uses the secret key, which runs as service_role
grant all on all tables in schema public to service_role;
alter default privileges in schema public grant all on tables to service_role;

grant select on public.recipes to anon, authenticated;

grant select, update on public.profiles to authenticated;
grant select on public.search_sessions to authenticated;
grant select on public.session_results to authenticated;
grant select on public.chat_messages to authenticated;
grant select on public.shopping_lists to authenticated;
grant select, insert, update, delete on public.saved_recipes to authenticated;
