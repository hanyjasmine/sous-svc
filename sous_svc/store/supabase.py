import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from supabase import Client

from ..recipes import Ingredient, Recipe, SearchFilters
from .base import Pool, RecipeNotFound, SearchSession

RECIPE_COLUMNS = (
    "id, source, source_id, source_url, title, author, image_url, summary, "
    "ingredients, steps, tags, total_minutes, servings"
)


class SupabaseStore:
    def __init__(self, client: Client, ttl_seconds: int):
        self._db = client
        self._ttl = ttl_seconds

    def create_session(self, query: str, pool: Pool, user_id: str | None) -> SearchSession:
        recipes = _dedupe(pool.recipes)
        db_ids = self._upsert_recipes(recipes)
        row = (
            self._db.table("search_sessions")
            .insert(
                {
                    "user_id": user_id,
                    "initial_query": query,
                    **_pool_columns(pool),
                }
            )
            .execute()
            .data[0]
        )
        self._insert_results(row["id"], recipes, db_ids, start=0)
        pool = Pool(pool.filters, recipes, pool.offset, pool.total)
        return SearchSession(row["id"], query, pool, user_id)

    def get_session(self, session_id: str) -> SearchSession | None:
        if not _is_uuid(session_id):
            return None
        res = (
            self._db.table("search_sessions")
            .select(
                "id, user_id, initial_query, filters, provider_offset, provider_total, "
                "web_status, web_started_at, "
                f"created_at, session_results(rank, visible, recipes({RECIPE_COLUMNS}))"
            )
            .eq("id", session_id)
            .maybe_single()
            .execute()
        )
        row = res.data if res else None
        if not row:
            return None

        created = datetime.fromisoformat(row["created_at"]).timestamp()
        # spoonacular terms cap result caching at an hour
        if datetime.now(UTC).timestamp() - created > self._ttl:
            return None

        results = sorted(row["session_results"], key=lambda r: r["rank"])
        recipes = [from_row(r["recipes"]) for r in results]
        hidden = any(not r["visible"] for r in results)
        filters = (
            SearchFilters.model_validate(row["filters"])
            if row.get("filters")
            else SearchFilters.from_raw(row["initial_query"])
        )
        return SearchSession(
            id=row["id"],
            query=row["initial_query"],
            pool=Pool(filters, recipes, row["provider_offset"], row["provider_total"]),
            user_id=row["user_id"],
            created_at=created,
            visible_ids=[rec.id for rec, r in zip(recipes, results, strict=True) if r["visible"]]
            if hidden
            else None,
            web_status=row.get("web_status"),
            web_started=datetime.fromisoformat(row["web_started_at"]).timestamp()
            if row.get("web_started_at")
            else None,
        )

    def extend_pool(
        self, session: SearchSession, recipes: list[Recipe], offset: int, total: int
    ) -> None:
        recipes = _dedupe(recipes)
        db_ids = self._upsert_recipes(recipes)
        self._insert_results(
            session.id,
            recipes,
            db_ids,
            start=len(session.pool.recipes),
            # new arrivals stay hidden while the chat has the list narrowed
            visible=not session.narrowed,
        )
        self._db.table("search_sessions").update(
            {"provider_offset": offset, "provider_total": total}
        ).eq("id", session.id).execute()

    def replace_pool(self, session: SearchSession, pool: Pool) -> None:
        recipes = _dedupe(pool.recipes)
        db_ids = self._upsert_recipes(recipes)
        self._db.table("session_results").delete().eq("session_id", session.id).execute()
        self._insert_results(session.id, recipes, db_ids, start=0)
        self._db.table("search_sessions").update(_pool_columns(pool)).eq("id", session.id).execute()

    def set_visible(self, session: SearchSession, recipe_ids: list[str] | None) -> None:
        if recipe_ids is None:
            self._db.table("session_results").update({"visible": True}).eq(
                "session_id", session.id
            ).execute()
            return
        wanted = set(recipe_ids)
        keep = [db_id for rid, db_id in self._db_ids(session.pool.recipes).items() if rid in wanted]
        self._db.table("session_results").update({"visible": False}).eq(
            "session_id", session.id
        ).execute()
        if keep:
            self._db.table("session_results").update({"visible": True}).eq(
                "session_id", session.id
            ).in_("recipe_id", keep).execute()

    def set_web_status(self, session_id: str, status: str) -> None:
        update: dict = {"web_status": status}
        if status == "pending":
            update["web_started_at"] = datetime.now(UTC).isoformat()
        self._db.table("search_sessions").update(update).eq("id", session_id).execute()

    def get_cached_pool(self, key: str) -> Pool | None:
        cutoff = datetime.now(UTC) - timedelta(seconds=self._ttl)
        res = (
            self._db.table("search_cache")
            .select("*")
            .eq("key", key)
            .gte("created_at", cutoff.isoformat())
            .maybe_single()
            .execute()
        )
        row = res.data if res else None
        if not row:
            return None
        ids = row["recipe_ids"]
        rows = (
            self._db.table("recipes").select(RECIPE_COLUMNS).in_("id", ids).execute().data
            if ids
            else []
        )
        by_id = {r["id"]: from_row(r) for r in rows}
        return Pool(
            SearchFilters.model_validate(row["filters"]),
            [by_id[i] for i in ids if i in by_id],
            row["provider_offset"],
            row["provider_total"],
        )

    def put_cached_pool(self, key: str, pool: Pool) -> None:
        recipes = _dedupe(pool.recipes)
        db_ids = self._db_ids(recipes)
        # a recipe can be missing if it was never persisted, upsert to be safe
        if len(db_ids) < len(recipes):
            db_ids = self._upsert_recipes(recipes)
        self._db.table("search_cache").upsert(
            {
                "key": key,
                "recipe_ids": [db_ids[r.id] for r in recipes],
                "created_at": datetime.now(UTC).isoformat(),
                **_pool_columns(pool),
            },
            on_conflict="key",
        ).execute()

    def list_messages(self, session_id: str) -> list[dict]:
        rows = (
            self._db.table("chat_messages")
            .select("role, content")
            .eq("session_id", session_id)
            .order("seq")
            .execute()
            .data
        )
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    def append_messages(self, session_id: str, messages: list[dict]) -> None:
        if messages:
            self._db.table("chat_messages").insert(
                [{"session_id": session_id, **m} for m in messages]
            ).execute()

    def list_saved(self, user_id: str) -> list[Recipe]:
        rows = (
            self._db.table("saved_recipes")
            .select(f"created_at, recipes({RECIPE_COLUMNS})")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
            .data
        )
        return [from_row(r["recipes"]) for r in rows]

    def save_recipe(self, user_id: str, recipe_id: str) -> None:
        db_id = self._recipe_db_id(recipe_id)
        self._db.table("saved_recipes").upsert(
            {"user_id": user_id, "recipe_id": db_id},
            on_conflict="user_id,recipe_id",
            ignore_duplicates=True,
        ).execute()

    def unsave_recipe(self, user_id: str, recipe_id: str) -> None:
        try:
            db_id = self._recipe_db_id(recipe_id)
        except RecipeNotFound:
            return
        self._db.table("saved_recipes").delete().eq("user_id", user_id).eq(
            "recipe_id", db_id
        ).execute()

    def _upsert_recipes(self, recipes: list[Recipe]) -> dict[str, str]:
        if not recipes:
            return {}
        rows = (
            self._db.table("recipes")
            .upsert([to_row(r) for r in recipes], on_conflict="source,source_id")
            .execute()
            .data
        )
        by_key = {(row["source"], row["source_id"]): row["id"] for row in rows}
        return {r.id: by_key[(r.source, source_id(r.id))] for r in recipes}

    # api id -> db uuid for recipes already stored
    def _db_ids(self, recipes: list[Recipe]) -> dict[str, str]:
        by_source: dict[str, list[str]] = defaultdict(list)
        for r in recipes:
            by_source[r.source].append(source_id(r.id))
        out: dict[str, str] = {}
        for source, sids in by_source.items():
            rows = (
                self._db.table("recipes")
                .select("id, source_id")
                .eq("source", source)
                .in_("source_id", sids)
                .execute()
                .data
            )
            out.update({f"{source}-{row['source_id']}": row["id"] for row in rows})
        return out

    def _insert_results(
        self,
        session_id: str,
        recipes: list[Recipe],
        db_ids: dict[str, str],
        start: int,
        visible: bool = True,
    ) -> None:
        if recipes:
            self._db.table("session_results").insert(
                [
                    {
                        "session_id": session_id,
                        "recipe_id": db_ids[r.id],
                        "rank": start + i,
                        "visible": visible,
                    }
                    for i, r in enumerate(recipes)
                ]
            ).execute()

    def _recipe_db_id(self, recipe_id: str) -> str:
        source, _, sid = recipe_id.partition("-")
        rows = (
            self._db.table("recipes")
            .select("id")
            .eq("source", source)
            .eq("source_id", sid)
            .limit(1)
            .execute()
            .data
        )
        if not rows:
            raise RecipeNotFound(recipe_id)
        return rows[0]["id"]


# api ids look like "spoonacular-637103", the db keeps source and source_id apart
def source_id(recipe_id: str) -> str:
    return recipe_id.partition("-")[2]


# duplicate ids in one upsert make postgres reject the whole batch
def _dedupe(recipes: list[Recipe]) -> list[Recipe]:
    return list({r.id: r for r in recipes}.values())


def _pool_columns(pool: Pool) -> dict:
    return {
        "filters": pool.filters.model_dump(),
        "provider_offset": pool.offset,
        "provider_total": pool.total,
    }


def to_row(r: Recipe) -> dict:
    return {
        "source": r.source,
        "source_id": source_id(r.id),
        "source_url": r.source_url,
        "title": r.title,
        "author": r.author,
        "image_url": r.image_url,
        "summary": r.summary,
        "ingredients": [i.to_dict() for i in r.ingredients],
        "steps": r.steps,
        "tags": r.tags,
        "total_minutes": r.total_minutes,
        "servings": r.servings,
        "fetched_at": datetime.now(UTC).isoformat(),
    }


def from_row(row: dict) -> Recipe:
    return Recipe(
        id=f"{row['source']}-{row['source_id']}",
        source=row["source"],
        title=row["title"],
        summary=row.get("summary") or "",
        ingredients=[Ingredient(**i) for i in row.get("ingredients") or []],
        steps=row.get("steps") or [],
        tags=row.get("tags") or [],
        source_url=row.get("source_url"),
        author=row.get("author"),
        image_url=row.get("image_url"),
        total_minutes=row.get("total_minutes"),
        servings=row.get("servings"),
    )


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True
