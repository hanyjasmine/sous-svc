import uuid
from datetime import UTC, datetime

from supabase import Client

from ..recipes import Ingredient, Recipe, SearchFilters
from .base import RecipeNotFound, SearchSession

RECIPE_COLUMNS = (
    "id, source, source_id, source_url, title, author, image_url, summary, "
    "ingredients, steps, tags, total_minutes, servings"
)


class SupabaseStore:
    def __init__(self, client: Client, ttl_seconds: int):
        self._db = client
        self._ttl = ttl_seconds

    def create_session(
        self, query: str, filters: SearchFilters, recipes: list[Recipe], user_id: str | None
    ) -> SearchSession:
        # duplicate ids in one upsert make postgres reject the whole batch
        recipes = list({r.id: r for r in recipes}.values())
        rows = (
            self._db.table("recipes")
            .upsert([to_row(r) for r in recipes], on_conflict="source,source_id")
            .execute()
            .data
            if recipes
            else []
        )
        db_ids = {(row["source"], row["source_id"]): row["id"] for row in rows}

        session_row = (
            self._db.table("search_sessions")
            .insert({"user_id": user_id, "initial_query": query, "filters": filters.model_dump()})
            .execute()
            .data[0]
        )
        if recipes:
            self._db.table("session_results").insert(
                [
                    {
                        "session_id": session_row["id"],
                        "recipe_id": db_ids[(r.source, source_id(r.id))],
                        "rank": rank,
                    }
                    for rank, r in enumerate(recipes)
                ]
            ).execute()

        return SearchSession(session_row["id"], query, filters, recipes, user_id)

    def get_session(self, session_id: str) -> SearchSession | None:
        if not _is_uuid(session_id):
            return None
        res = (
            self._db.table("search_sessions")
            .select(
                "id, user_id, initial_query, filters, created_at, "
                f"session_results(rank, visible, recipes({RECIPE_COLUMNS}))"
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

        results = sorted(
            (r for r in row["session_results"] if r["visible"]), key=lambda r: r["rank"]
        )
        return SearchSession(
            id=row["id"],
            query=row["initial_query"],
            filters=SearchFilters.model_validate(row["filters"])
            if row.get("filters")
            else SearchFilters.from_raw(row["initial_query"]),
            recipes=[from_row(r["recipes"]) for r in results],
            user_id=row["user_id"],
            created_at=created,
        )

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
