# sous-svc

Flask backend for [Sous](../sous). Owns the Claude calls and tool loop, recipe providers, Supabase data access and Twilio. The Next.js app proxies `/api/svc/*` here.

## Run

```sh
cp .env.example .env
uv sync
uv run flask --app sous_svc run --port 5001 --debug
```

Port 5001 because macOS AirPlay holds 5000. The frontend reads `SOUS_SVC_URL` (default `http://127.0.0.1:5001`).

## Endpoints

| Method | Path | Notes |
|---|---|---|
| `GET` | `/health` | Status and feature flags |
| `POST` | `/search` | `{"query": "..."}`. Claude turns the query into Spoonacular filters, then a pool of up to 30 recipes is fetched (or reused from the cache) and stored as a session. Returns the first page. `429` when the Spoonacular daily quota is used up |
| `GET` | `/sessions/<id>` | Session plus the first page of its current view |
| `GET` | `/sessions/<id>/results?cursor=N` | Next page of the view. Scrolling past the pool pulls 20 more from Spoonacular, up to 90 per session |
| `GET` | `/sessions/<id>/messages` | Chat history as display bubbles |
| `POST` | `/sessions/<id>/chat` | `{"greet": true}` once to open with the original query, then `{"message": "..."}`. Streams server-sent events: `text`, `results` (a new first page), `error`, `done` |

### Result pool and cache

- A search builds a pool of up to 30 recipes. The view is the whole pool, or a subset the chat narrowed it to.
- Pools are cached for an hour by a hash of the parsed filters (`search_cache`), so a repeat search costs no points. An hour is Spoonacular's caching limit, and sessions expire on the same `SESSION_TTL_SECONDS`.
- A 30-result search costs a few Spoonacular points (full recipe info is charged per result). The free plan has 50 points a day.

### Chat

Each turn, Claude gets the pool as a mid-conversation system message (only when it changed) and picks one of three tools:

- `narrow_results`: shows a subset of the pool. Free.
- `show_all_results`: undoes the narrowing.
- `new_search`: replaces the pool. Spends points.

Claude can make up to 4 calls per turn. Full content blocks (including thinking) are stored in `chat_messages`, so history replays exactly. Guests are limited by `GUEST_CHAT_TURNS_PER_HOUR`, and each chat is capped at 30 turns.

## Test

```sh
uv run pytest
uv run ruff check .
```

## Database

Schema lives in `supabase/migrations/`. Apply it with the Supabase CLI (`supabase db push`) or paste it into the SQL editor.

## Feature flags

| Flag | Default | Notes |
|---|---|---|
| `WEB_SEARCH_FALLBACK_ENABLED` | `true` | Claude web search + schema.org JSON-LD when the recipe API comes back thin |
| `NYT_MCP_ENABLED` | `false` | NYT Cooking search via the unofficial `nyt-cooking-mcp` server |
| `NYT_MCP_FULL_RECIPES` | `false` | Full NYT ingredients/steps. Personal use only; keep off on any public deploy |
