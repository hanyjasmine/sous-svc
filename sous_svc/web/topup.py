import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import httpx

from ..pool import PoolResult, cache_pool
from ..recipes import Recipe
from ..store import Pool, SearchSession, Store
from .fetch import FetchError, fetch_html
from .finder import find_recipe_urls
from .jsonld import canonical_url, extract_recipe

log = logging.getLogger(__name__)

PAGE_WORKERS = 6


class WebTopup:
    def __init__(
        self,
        store: Store,
        model: str,
        enabled: bool,
        below: int,
        find_urls: Callable[..., list[str]] = find_recipe_urls,
        fetch: Callable[[str, httpx.Client], str] = fetch_html,
        submit: Callable | None = None,
    ):
        self.store = store
        self.model = model
        self.enabled = enabled
        self.below = below
        self._find_urls = find_urls
        self._fetch = fetch
        self._http = httpx.Client(timeout=10)
        self._jobs = ThreadPoolExecutor(max_workers=2, thread_name_prefix="web-topup")
        # tests run jobs inline
        self._submit = submit or self._jobs.submit

    # thin or failed provider results get filled in from the web
    def needed(self, result: PoolResult) -> bool:
        return self.enabled and result.fresh and len(result.pool.recipes) < self.below

    def start(self, session: SearchSession, result: PoolResult, request: str) -> None:
        self.store.set_web_status(session.id, "pending")
        session.web_status = "pending"
        session.web_started = time.time()
        self._submit(self._run, session.id, result, request)

    def _run(self, session_id: str, result: PoolResult, request: str) -> None:
        try:
            recipes = self.collect(result.pool, request)
            self._apply(session_id, result, recipes)
        except Exception:
            log.exception("web topup failed for %s", session_id)
        finally:
            try:
                self.store.set_web_status(session_id, "done")
            except Exception:
                log.exception("couldn't mark web topup done for %s", session_id)

    def collect(self, pool: Pool, request: str) -> list[Recipe]:
        have = {canonical_url(r.source_url) for r in pool.recipes if r.source_url}
        urls = self._find_urls(request, pool.filters, self.model, [r.title for r in pool.recipes])
        urls = [u for u in dict.fromkeys(urls) if canonical_url(u) not in have]
        with ThreadPoolExecutor(max_workers=PAGE_WORKERS) as pages:
            found = [r for r in pages.map(self._recipe_from, urls) if r]
        log.info("web topup: %d of %d pages had a recipe", len(found), len(urls))
        return list({r.id: r for r in found}.values())

    def _recipe_from(self, url: str) -> Recipe | None:
        try:
            return extract_recipe(self._fetch(url, self._http), url)
        except (FetchError, httpx.HTTPError) as e:
            log.info("skipping %s: %s", url, e)
            return None

    def _apply(self, session_id: str, result: PoolResult, recipes: list[Recipe]) -> None:
        session = self.store.get_session(session_id)
        # the chat may have swapped in a different search meanwhile
        if not session or session.filters.cache_key() != result.pool.filters.cache_key():
            return
        seen = {r.id for r in session.pool.recipes}
        fresh = [r for r in recipes if r.id not in seen]
        pool = session.pool
        if fresh:
            self.store.extend_pool(session, fresh, pool.offset, pool.total)
        full = Pool(pool.filters, [*pool.recipes, *fresh], pool.offset, pool.total)
        cache_pool(self.store, PoolResult(full, result.cache_keys, fresh=True))
