import hashlib
import html
import json
import re
from urllib.parse import urlparse

from ..recipes.models import Ingredient, Recipe
from ..recipes.spoonacular import clean_summary

_SCRIPT = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.S | re.I
)
_TAGS = re.compile(r"<[^>]+>")
_DURATION = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?", re.I)
# "1.", "1)", "Step 1:" at the start of a step
_NUMBERING = re.compile(r"^(step\s*)?\d+\s*[.):]\s*", re.I)
_NUMBER = re.compile(r"\d+")
_PARENS = re.compile(r"\([^()]*\)")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
# sites word the same tag differently, match spoonacular's wording
_TAG_ALIASES = {
    "side": "side dish",
    "sides": "side dish",
    "side dishes": "side dish",
    "main": "main course",
    "main dish": "main course",
    "main dishes": "main course",
    "desserts": "dessert",
    "breads": "bread",
    "united states": "american",
    "usa": "american",
    "gluten-free": "gluten free",
    "dairy-free": "dairy free",
}
_AMOUNT = r"[\d½⅓⅔¼¾⅛⅜⅝⅞]+(?:[./]\d+)?"
_UNITS = (
    r"cups?|c\.|tablespoons?|tbsps?\.?|tbs\.?|teaspoons?|tsps?\.?|ounces?|oz\.?|pounds?|lbs?\.?"
    r"|grams?|g|kilograms?|kg|milliliters?|ml|liters?|l|cloves?|pinch(?:es)?|dash(?:es)?"
    r"|cans?|packages?|sticks?|slices?|bunch(?:es)?|quarts?|pints?"
)
# "2 1/2 cups (300g) flour" -> quantity "2 1/2 cups", name "flour"
_INGREDIENT = re.compile(
    rf"^\s*((?:{_AMOUNT}(?:\s*-\s*{_AMOUNT})?\s*)+(?:(?:{_UNITS})\b\.?)?)\s*(?:\([^)]*\)\s*)?(.+)$",
    re.I,
)


def extract_recipe(page: str, url: str) -> Recipe | None:
    for raw in _SCRIPT.findall(page):
        try:
            data = json.loads(raw.strip())
        except ValueError:
            continue
        if node := find_recipe_node(data):
            return to_recipe(node, url)
    return None


def find_recipe_node(data) -> dict | None:
    if isinstance(data, list):
        for item in data:
            if found := find_recipe_node(item):
                return found
        return None
    if not isinstance(data, dict):
        return None
    types = data.get("@type")
    if "Recipe" in (types if isinstance(types, list) else [types]):
        return data
    for key in ("@graph", "mainEntity"):
        if found := find_recipe_node(data.get(key)):
            return found
    return None


def to_recipe(node: dict, url: str) -> Recipe | None:
    title = _text(node.get("name"))
    lines = _list(node.get("recipeIngredient"), split=False)
    ingredients = [parse_ingredient(_text(i)) for i in lines]
    ingredients = [i for i in ingredients if i.name]
    steps = to_steps(node.get("recipeInstructions"))
    # a page without the actual recipe isn't worth showing
    if not title or len(ingredients) < 2 or not steps:
        return None

    url = canonical_url(url)
    minutes = duration_minutes(node.get("totalTime")) or (
        (duration_minutes(node.get("prepTime")) or 0)
        + (duration_minutes(node.get("cookTime")) or 0)
    )
    return Recipe(
        id=web_id(url),
        source="web",
        title=title,
        summary=clean_summary(_text(node.get("description"))),
        ingredients=ingredients,
        steps=steps,
        tags=to_tags(node, minutes),
        source_url=url,
        author=_author(node.get("author")) or _site_name(url),
        image_url=_image(node.get("image")),
        total_minutes=minutes or None,
        servings=_servings(node.get("recipeYield")),
    )


def parse_ingredient(line: str) -> Ingredient:
    quantity = None
    if match := _INGREDIENT.match(line):
        quantity, line = match.group(1).strip(), match.group(2)
    # recipe card plugins wrap notes like "((divided))" around names, peel them innermost first
    while (stripped := _PARENS.sub("", line)) != line:
        line = stripped
    name = " ".join(line.replace("*", "").split()).strip(" ,")
    return Ingredient(name=name, quantity=quantity)


def to_steps(value) -> list[str]:
    out: list[str] = []

    def walk(v):
        if isinstance(v, str):
            for line in re.split(r"\n+|<br\s*/?>|</p>", v):
                if step := _NUMBERING.sub("", _text(line)):
                    out.append(step)
        elif isinstance(v, list):
            for item in v:
                walk(item)
        elif isinstance(v, dict):
            # sections nest their steps under itemListElement
            if "itemListElement" in v:
                walk(v["itemListElement"])
            else:
                walk(v.get("text") or v.get("name") or "")

    walk(value)
    # one long paragraph with nothing better to go on, split by sentence
    if len(out) == 1 and len(out[0]) > 200:
        return [s for s in _SENTENCE.split(out[0]) if s]
    return out


def duration_minutes(value) -> int | None:
    if not isinstance(value, str) or not (match := _DURATION.fullmatch(value.strip())):
        return None
    days, hours, minutes = (int(g or 0) for g in match.groups())
    return days * 1440 + hours * 60 + minutes or None


def to_tags(node: dict, minutes: int | None) -> list[str]:
    tags = [*_list(node.get("recipeCuisine")), *_list(node.get("recipeCategory"))]
    if minutes and minutes <= 30:
        tags.append("under 30 min")
    seen: list[str] = []
    for tag in (_TAG_ALIASES.get(t, t) for t in (_text(t).lower() for t in tags)):
        if tag and len(tag) <= 24 and tag not in seen:
            seen.append(tag)
    return seen[:5]


# same page with tracking params or a trailing slash is the same recipe
def canonical_url(url: str) -> str:
    parts = urlparse(url)
    host = (parts.hostname or "").removeprefix("www.")
    return f"https://{host}{parts.path.rstrip('/')}"


def web_id(url: str) -> str:
    return f"web-{hashlib.sha1(canonical_url(url).encode()).hexdigest()[:16]}"


def _text(value) -> str:
    if isinstance(value, dict):
        value = value.get("text") or value.get("name") or ""
    if not isinstance(value, str | int | float):
        return ""
    return " ".join(html.unescape(_TAGS.sub(" ", str(value))).split())


# tags often arrive as one comma-separated string
def _list(value, split: bool = True) -> list:
    if value is None:
        return []
    if isinstance(value, str) and split:
        return [v for v in value.split(",") if v.strip()]
    return value if isinstance(value, list) else [value]


def _author(value) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    return _text(value) or None


def _image(value) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl")
    if isinstance(value, str) and value.startswith(("https://", "http://")):
        return value
    return None


def _servings(value) -> int | None:
    for item in _list(value) if not isinstance(value, int) else [value]:
        if match := _NUMBER.search(str(item)):
            return int(match.group())
    return None


def _site_name(url: str) -> str:
    return urlparse(url).hostname or ""
