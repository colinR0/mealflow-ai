"""Dish-name aliases for retrieval queries.

The knowledge library names dishes the way its sources do ("西红柿炒鸡蛋"), while
users type the colloquial name ("番茄炒蛋"). The keyword channel is lexical, so a
term the library never wrote cannot match, and a 512-dimension Chinese model does
not reliably equate the two either.

Only *ingredient synonyms* and *well-established dish spellings* belong here. It
is deliberately a small hand-curated list: a wrong alias silently returns another
dish, which is worse than returning nothing. Entries are bidirectional so the
library form also reaches the user's wording.

This is intentionally separate from ``ingredient_aliases``: that table maps an
ingredient to its canonical key ("草鱼" -> fish), which would rewrite the stem of
"清蒸草鱼" and make it match unrelated fish recipes.

Each alias must survive tokenization as a single search term, otherwise it is a
dead entry that can never match an indexed token. ``test_dish_aliases.py``
enforces that, so an unreachable alias fails the build instead of silently doing
nothing.
"""

from __future__ import annotations

# Each group is interchangeable. The first item is the preferred display form.
_GROUPS: tuple[tuple[str, ...], ...] = (
    ("西红柿", "番茄", "蕃茄"),
    ("土豆", "马铃薯", "洋芋"),
    ("黄瓜", "青瓜"),
    ("西兰花", "青花菜"),
    ("花菜", "菜花", "花椰菜"),
    ("胡萝卜", "红萝卜"),
    ("红薯", "地瓜", "番薯"),
    ("玉米", "苞米"),
    ("香菜", "芫荽"),
    ("卷心菜", "包菜", "圆白菜", "洋白菜"),
    # Lexeme-level spellings, not surface names: Jieba cuts "宫保鸡丁" into
    # "宫保鸡" + "丁" and "木樨肉" into "木樨" + "肉", so the indexed term for the
    # dish is the stem. Listing the stem is what makes the mapping reachable.
    ("宫保鸡", "宫爆鸡丁"),
    ("木须肉", "木樨"),
)

_ALIASES: dict[str, tuple[str, ...]] = {
    term: tuple(other for other in group if other != term)
    for group in _GROUPS
    for term in group
}


def alias_variants(term: str) -> tuple[str, ...]:
    """Return interchangeable names for one term, excluding the term itself."""
    return _ALIASES.get(term, ())


def alias_groups() -> tuple[tuple[str, ...], ...]:
    """Expose the curated groups so tests can prove every alias stays reachable."""
    return _GROUPS
