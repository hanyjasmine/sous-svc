import html
import re
from fractions import Fraction

import httpx

from .filters import SearchFilters
from .models import Ingredient, Recipe

BASE_URL = "https://api.spoonacular.com"

# spoonacular serves the same image at several sizes, 636x393 is the largest
_IMAGE_SIZE = re.compile(r"-\d+x\d+\.(\w+)$")
_TAGS = re.compile(r"<[^>]+>")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_LIST_ITEM = re.compile(r"<li[^>]*>(.*?)</li>", re.S | re.I)
_BREAK = re.compile(r"</?p[^>]*>|<br\s*/?>|\n+", re.I)
# "1.", "1)", "Step 1:" at the start of a source step
_NUMBERING = re.compile(r"^(step\s*)?\d+\s*[.):]\s*", re.I)


class ProviderError(Exception):
    pass


class QuotaExceeded(ProviderError):
    pass


class SpoonacularClient:
    def __init__(self, api_key: str, http: httpx.Client | None = None):
        self._http = http or httpx.Client(base_url=BASE_URL, timeout=15)
        self._api_key = api_key

    def search(self, filters: SearchFilters, number: int = 10) -> list[Recipe]:
        if not self._api_key:
            raise ProviderError("SPOONACULAR_API_KEY is not set")
        params = to_params(filters) | {
            "number": number,
            "addRecipeInformation": "true",
            "fillIngredients": "true",
            "instructionsRequired": "true",
        }
        try:
            res = self._http.get(
                "/recipes/complexSearch", params=params, headers={"x-api-key": self._api_key}
            )
        except httpx.HTTPError as e:
            raise ProviderError(f"spoonacular request failed: {e}") from e

        # 402 means the daily point quota is used up
        if res.status_code == 402:
            raise QuotaExceeded("spoonacular daily quota used up, resets at midnight utc")
        if res.status_code != 200:
            raise ProviderError(f"spoonacular returned {res.status_code}")

        return [to_recipe(r) for r in res.json().get("results", [])]


def to_params(f: SearchFilters) -> dict:
    params: dict = {"query": f.query} if f.query.strip() else {}
    if f.cuisines:
        params["cuisine"] = ",".join(f.cuisines)
    if f.diets:
        params["diet"] = ",".join(f.diets)
    if f.intolerances:
        params["intolerances"] = ",".join(f.intolerances)
    if f.include_ingredients:
        params["includeIngredients"] = ",".join(f.include_ingredients)
    if f.exclude_ingredients:
        params["excludeIngredients"] = ",".join(f.exclude_ingredients)
    if f.meal_type:
        params["type"] = f.meal_type
    if f.max_ready_minutes:
        params["maxReadyTime"] = f.max_ready_minutes
    return params


def to_recipe(r: dict) -> Recipe:
    return Recipe(
        id=f"spoonacular-{r['id']}",
        source="spoonacular",
        title=r.get("title", "").strip(),
        summary=clean_summary(r.get("summary", "")),
        ingredients=[to_ingredient(i) for i in r.get("extendedIngredients") or []],
        steps=to_steps(r),
        tags=to_tags(r),
        source_url=r.get("sourceUrl"),
        author=r.get("sourceName") or None,
        image_url=_IMAGE_SIZE.sub(r"-636x393.\1", r["image"]) if r.get("image") else None,
        total_minutes=r.get("readyInMinutes"),
        servings=r.get("servings"),
    )


def to_steps(r: dict) -> list[str]:
    # the source's own instructions keep punctuation and fractions intact,
    # spoonacular's analyzed split sometimes cuts mid-sentence ("reduced by 1/")
    raw = r.get("instructions") or ""
    chunks = _LIST_ITEM.findall(raw) or _BREAK.split(raw)
    steps = [s for s in (_clean_step(c) for c in chunks) if s]
    if len(steps) >= 2:
        return steps

    analyzed = [
        s["step"]
        for block in r.get("analyzedInstructions") or []
        for s in block.get("steps", [])
        if s.get("step")
    ]
    if analyzed:
        # spoonacular already stripped numbering here, so keep leading digits
        return _repair_steps([_clean_step(s, numbered=False) for s in analyzed])

    # one long paragraph with nothing better to go on, split by sentence
    return [s for s in _SENTENCE.split(steps[0]) if s] if steps else []


def _clean_step(raw: str, numbered: bool = True) -> str:
    text = " ".join(html.unescape(_TAGS.sub(" ", raw)).split())
    if numbered:
        text = _NUMBERING.sub("", text)
    # a space sneaks in before punctuation when tags are stripped
    return re.sub(r"\s+([.,;:!?])", r"\1", text)


def _repair_steps(steps: list[str]) -> list[str]:
    out: list[str] = []
    for step in filter(None, steps):
        prev = out[-1] if out else ""
        # rejoin a step that was split mid-fraction or mid-sentence
        if prev and (prev.endswith("/") or prev[-1].isdigit() or step[0].islower()):
            out[-1] = f"{prev}{'' if prev.endswith('/') else ' '}{step}"
        else:
            out.append(step)
    return [s if s[-1] in ".!?)" else f"{s}." for s in out]


def to_ingredient(i: dict) -> Ingredient:
    us = (i.get("measures") or {}).get("us") or {}
    amount = us.get("amount", i.get("amount"))
    unit = us.get("unitShort", i.get("unit", ""))
    quantity = " ".join(p for p in [format_amount(amount), unit] if p) or None
    return Ingredient(
        name=i.get("nameClean") or i.get("name", ""),
        quantity=quantity,
        aisle=(i.get("aisle") or "").split(";")[0].lower() or None,
    )


def format_amount(amount: float | None) -> str:
    if not amount:
        return ""
    # snap to kitchen-friendly fractions like 1/3 or 1 1/2
    frac = Fraction(amount).limit_denominator(8)
    whole, rest = divmod(frac.numerator, frac.denominator)
    if rest == 0:
        return str(whole)
    part = f"{rest}/{frac.denominator}"
    return f"{whole} {part}" if whole else part


def clean_summary(raw: str, sentences: int = 2) -> str:
    text = html.unescape(_TAGS.sub("", raw)).strip()
    # spoonacular summaries mix in rhetorical questions like "watching your figure?"
    kept = [s for s in _SENTENCE.split(text) if s and not s.endswith("?")]
    return " ".join(kept[:sentences])


# most useful dish types first, spoonacular lists "lunch" before "main course"
_DISH_TYPES = ["main course", "dessert", "breakfast", "side dish", "soup", "salad", "snack"]


def to_tags(r: dict) -> list[str]:
    dish = next((d for d in _DISH_TYPES if d in r.get("dishTypes", [])), None)
    tags = [*r.get("diets", []), *r.get("cuisines", [])[:1], *([dish] if dish else [])]
    if (r.get("readyInMinutes") or 99) <= 30:
        tags.append("under 30 min")
    seen: list[str] = []
    for t in (t.lower() for t in tags):
        if t not in seen:
            seen.append(t)
    return seen[:5]
