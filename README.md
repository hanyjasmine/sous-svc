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
| `POST` | `/search` | `{"query": "..."}`. Claude turns the query into Spoonacular filters, results are stored as a session. `429` when the Spoonacular daily quota is used up |
| `GET` | `/sessions/<id>` | A stored search. Sessions live in memory for `SESSION_TTL_SECONDS` (1 hour, Spoonacular's caching limit) and vanish on restart until Supabase lands |

Each search costs about 2 Spoonacular points (10 results with full recipe info). The free plan has 50 points a day.

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
