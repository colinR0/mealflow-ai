"""Normalize untrusted ingredient labels into single food names.

Model output and web summaries sometimes merge a parenthetical descriptor into
the name ("荔浦芋头、实惠新鲜）"). Only the leading food fragment is a valid
ingredient name; descriptors and kitchenware must not reach inventory checks,
procurement drafts, or the rendered answer.
"""

from __future__ import annotations

import re

_BRACKETED = re.compile(r"[（(【［\[][^）)】］\]]*[）)】］\]]")
_OPEN_BRACKET = re.compile(r"[（(【［\[].*$")
_SEPARATOR = re.compile(r"[、，,；;]")
_TRAILING_QUANTITY = re.compile(
    r"\s*(?:约|大约)?\s*\d+(?:\.\d+)?\s*"
    r"(?:克|千克|公斤|g|kg|斤|两|个|只|根|颗|瓣|片|勺|茶匙|汤匙|毫升|ml|升|l)"
    r"\s*(?:左右)?\s*$",
    re.IGNORECASE,
)
_LEADING_DESCRIPTOR = re.compile(
    r"^(?:实惠|新鲜|优质|精选|特级|一级|农家自种|农家|自种|有机|现摘|现挖|"
    r"特价|促销|包邮|冷冻|速冻|去皮|切块|切片|冬季|春季|夏季|秋季|"
    r"冬天|夏天|春天|秋天|当季|应季)+"
)
_TRAILING_DESCRIPTOR = re.compile(
    r"(?:实惠|新鲜|优质|精选|特价|促销|包邮|现摘|现挖|冷冻|速冻|去皮|切块|切片)+$"
)
_BRACKET_CHARS = re.compile(r"[（）()【】［］\[\]]")
# A fragment containing a note phrase is a parenthetical note, not a food name.
_NOTE_PHRASE = re.compile(r"(?:用于|用来|点缀|装饰|摆盘|最佳|最好|可选|按需|酌量|推荐)")
_FORBIDDEN_CHARS = re.compile(r"[（）()【】［］\[\]、，,；;]")
# Kitchenware is noise in a recipe, but it is a legitimate thing to buy.
_KITCHENWARE = frozenset({"盆", "锅", "碗", "盘", "碟", "勺", "筷子", "案板", "刀"})
_PLACEHOLDER = frozenset({
    "适量", "少许", "若干", "按口味", "调味料", "配料", "主料", "辅料",
})
_NON_FOOD = _KITCHENWARE | _PLACEHOLDER
_MAX_NAME_LENGTH = 20


def strip_parenthetical_notes(value: str) -> str:
    """Remove paired parenthetical notes from a free-text ingredient summary."""
    return _BRACKETED.sub("", str(value or ""))


def _clean_fragments(value: str, *, blocked: frozenset[str]) -> list[str]:
    text = strip_parenthetical_notes(value).strip()
    if not text:
        return []
    text = _OPEN_BRACKET.sub("", text)
    names: list[str] = []
    for raw in _SEPARATOR.split(text):
        fragment = raw.strip()
        # A closing bracket without its pair means the fragment came from a
        # parenthetical note ("冬季黄心白菜最佳）"), not from a food name.
        if not fragment or _BRACKET_CHARS.search(fragment) or _NOTE_PHRASE.search(fragment):
            continue
        name = _TRAILING_QUANTITY.sub("", fragment).strip()
        name = _LEADING_DESCRIPTOR.sub("", name).strip()
        name = _TRAILING_DESCRIPTOR.sub("", name).strip()
        name = re.sub(r"\s+", " ", name).strip(" ，,。；;：:、-—/")
        if (
            not name
            or name in blocked
            or len(name) > _MAX_NAME_LENGTH
            or _FORBIDDEN_CHARS.search(name)
        ):
            continue
        if name not in names:
            names.append(name)
    return names


def clean_ingredient_names(value: str) -> list[str]:
    """Return single food names, dropping descriptors and non-food fragments.

    Separators start a new fragment, so "荔浦芋头、实惠新鲜）" keeps only
    "荔浦芋头". A fragment that still contains brackets or separators after
    cleaning is rejected instead of being truncated silently.
    """
    return _clean_fragments(value, blocked=_NON_FOOD)


def clean_purchase_names(value: str) -> list[str]:
    """Return buyable item names: notes go, kitchenware stays.

    A shopping list may legitimately contain 盆 or 保鲜膜, so only placeholder
    fragments ("适量") and parenthetical notes are rejected here.
    """
    return _clean_fragments(value, blocked=_PLACEHOLDER)


def clean_corpus_names(value: str) -> list[str]:
    """Canonicalize a stored corpus label without losing a possible allergen.

    Notes, kitchenware and placeholders are dropped. A label rejected only for
    being over-long is kept as-is, because these names also feed the household
    restriction check and silently dropping one could hide an allergen.
    """
    text = str(value or "").strip()
    if not text:
        return []
    names = _clean_fragments(text, blocked=_NON_FOOD)
    if names:
        return names
    if text in _NON_FOOD or _BRACKET_CHARS.search(text) or _NOTE_PHRASE.search(text):
        return []
    return [text]