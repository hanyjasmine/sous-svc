-- sous initial schema
-- sous-svc talks to postgres with the secret key (bypasses rls)
-- rls below protects anything that reads with a user's token

create table public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  display_name text,
  avatar_url text,
  phone_e164 text,
  phone_verified_at timestamptz,
  created_at timestamptz not null default now()
);

-- normalized recipes from every provider, doubles as the server-side cache
create table public.recipes (
  id uuid primary key default gen_random_uuid(),
  source text not null check (source in ('spoonacular', 'web', 'nyt')),
  source_id text not null,
  source_url text,
  title text not null,
  author text,
  image_url text,
  summary text,
  ingredients jsonb not null default '[]'::jsonb,
  steps jsonb not null default '[]'::jsonb,
  tags text[] not null default '{}',
  total_minutes int,
  servings int,
  fetched_at timestamptz not null default now(),
  unique (source, source_id)
);

-- user_id is null for guests, guest_token lets a guest claim sessions after sign-in
create table public.search_sessions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users (id) on delete cascade,
  guest_token text,
  initial_query text not null,
  created_at timestamptz not null default now()
);
create index search_sessions_user_idx on public.search_sessions (user_id, created_at desc);
create index search_sessions_guest_idx on public.search_sessions (guest_token);

create table public.session_results (
  session_id uuid not null references public.search_sessions (id) on delete cascade,
  recipe_id uuid not null references public.recipes (id) on delete cascade,
  rank int not null,
  visible boolean not null default true,
  primary key (session_id, recipe_id)
);

-- content keeps the full claude content blocks so tool turns replay exactly
create table public.chat_messages (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references public.search_sessions (id) on delete cascade,
  role text not null check (role in ('user', 'assistant')),
  content jsonb not null,
  created_at timestamptz not null default now()
);
create index chat_messages_session_idx on public.chat_messages (session_id, created_at);

create table public.saved_recipes (
  user_id uuid not null references auth.users (id) on delete cascade,
  recipe_id uuid not null references public.recipes (id) on delete cascade,
  notes text,
  created_at timestamptz not null default now(),
  primary key (user_id, recipe_id)
);

create table public.shopping_lists (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  recipe_ids uuid[] not null,
  items jsonb not null,
  sent_to text,
  sent_at timestamptz,
  created_at timestamptz not null default now()
);

-- row level security
alter table public.profiles enable row level security;
alter table public.recipes enable row level security;
alter table public.search_sessions enable row level security;
alter table public.session_results enable row level security;
alter table public.chat_messages enable row level security;
alter table public.saved_recipes enable row level security;
alter table public.shopping_lists enable row level security;

create policy "recipes are public" on public.recipes
  for select using (true);

create policy "own profile" on public.profiles
  for all using (auth.uid() = id) with check (auth.uid() = id);

create policy "own sessions" on public.search_sessions
  for select using (auth.uid() = user_id);

create policy "own session results" on public.session_results
  for select using (
    exists (
      select 1 from public.search_sessions s
      where s.id = session_id and s.user_id = auth.uid()
    )
  );

create policy "own chat messages" on public.chat_messages
  for select using (
    exists (
      select 1 from public.search_sessions s
      where s.id = session_id and s.user_id = auth.uid()
    )
  );

create policy "own saved recipes" on public.saved_recipes
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

create policy "own shopping lists" on public.shopping_lists
  for select using (auth.uid() = user_id);

-- create a profile row on sign-up
create function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = ''
as $$
begin
  insert into public.profiles (id, display_name, avatar_url)
  values (
    new.id,
    new.raw_user_meta_data ->> 'full_name',
    new.raw_user_meta_data ->> 'avatar_url'
  );
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
