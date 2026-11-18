import logging
from dataclasses import dataclass

from .recipes import ProviderError, Recipe, SearchFilters
from .store import Pool, SearchSession, Store

log = logging.getLogger(__name__)

# first fetch, enough to narrow over without a second call
POOL_SIZE = 30
# pulled in when the user scrolls past the pool
EXTEND_BY = 20
# hard cap so one session can't drain the daily quota
MAX_POOL = 90
PAGE_SIZE = 10


@dataclass
class PoolResult:
    pool: Pool
    cache_key: str
    fresh: bool


def find_pool(filters: SearchFilters, provider, store: Store) -> PoolResult:
    key = filters.cache_key()
    if cached := store.get_cached_pool(key):
        return PoolResult(cached, key, fresh=False)

    page = provider.search(filters, number=POOL_SIZE)
    # soft filters can zero things out, retry once without them
    if not page.recipes and filters.is_relaxable():
        filters = filters.relaxed()
        page = provider.search(filters, number=POOL_SIZE)
    pool = Pool(filters, _dedupe(page.recipes), offset=POOL_SIZE, total=page.total)
    return PoolResult(pool, key, fresh=True)


# call after the pool is persisted so the cache can point at stored recipes
def cache_pool(store: Store, result: PoolResult) -> None:
    if result.fresh and result.pool.recipes:
        store.put_cached_pool(result.cache_key, result.pool)


def can_extend(session: SearchSession) -> bool:
    pool = session.pool
    return not session.narrowed and pool.has_more and len(pool.recipes) < MAX_POOL


def extend(session: SearchSession, provider, store: Store) -> None:
    pool = session.pool
    try:
        page = provider.search(pool.filters, number=EXTEND_BY, offset=pool.offset)
    except ProviderError as e:
        # keep what we have, the list just stops growing
        log.warning("pool extend failed: %s", e)
        pool.total = pool.offset
        return
    seen = {r.id for r in pool.recipes}
    fresh = [r for r in _dedupe(page.recipes) if r.id not in seen]
    offset = pool.offset + EXTEND_BY
    store.extend_pool(session, fresh, offset, page.total)
    pool.recipes.extend(fresh)
    pool.offset, pool.total = offset, page.total


def page(
    session: SearchSession, provider, store: Store, cursor: int = 0, limit: int = PAGE_SIZE
) -> dict:
    view = session.view()
    if cursor + limit > len(view) and can_extend(session):
        extend(session, provider, store)
        view = session.view()
    end = cursor + limit
    more = end < len(view) or can_extend(session)
    return {
        "recipes": [r.to_dict() for r in view[cursor:end]],
        "nextCursor": end if more else None,
        "total": len(view),
        "poolSize": len(session.pool.recipes),
        "narrowed": session.narrowed,
    }


def session_dict(session: SearchSession, provider, store: Store) -> dict:
    return {
        "id": session.id,
        "query": session.query,
        "filters": session.filters.model_dump(),
        **page(session, provider, store),
    }


def _dedupe(recipes: list[Recipe]) -> list[Recipe]:
    return list({r.id: r for r in recipes}.values())
