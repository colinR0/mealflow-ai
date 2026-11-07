"""Ingredient identity helpers for inventory upsert and dictionary search."""

from __future__ import annotations

from hashlib import sha256
from uuid import UUID, uuid5

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import Ingredient, IngredientAlias

# Stable namespace for deterministic custom ingredient ids.
_CUSTOM_NS = UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

# Preset aliases keyed by Ingredient.key. Exact-match only after normalize_label.
PRESET_ALIASES: dict[str, tuple[str, ...]] = {
    "tomato": ("西红柿", "蕃茄"),
    "potato": ("马铃薯", "洋芋"),
    "greens": ("小青菜", "青菜叶"),
    "pepper_green": ("青辣椒", "柿子椒"),
    "carrot": ("红萝卜",),
    "onion": ("葱头",),
    "mushroom": ("冬菇",),
    "cucumber": ("青瓜",),
    "eggplant": ("矮瓜", "茄瓜"),
    "garlic": ("蒜头",),
    "ginger": ("姜",),
    "scallion": ("葱", "香葱"),
    "pork": ("猪肉", "前腿肉"),
    "chicken_breast": ("鸡胸",),
    "beef": ("牛肉",),
    "egg": ("鸡子儿",),
    "milk": ("鲜牛奶",),
    "fish": ("鱼肉",),
    "shrimp": ("虾",),
    "tofu": ("豆干", "老豆腐", "嫩豆腐"),
    "rice": ("米", "大米粒"),
    "noodles": ("面条",),
    "flour": ("小麦粉",),
    "soy_sauce": ("生抽", "老抽"),
    "oil": ("菜籽油", "花生油"),
    "vinegar": ("醋", "陈醋"),
}


class IngredientNotFoundError(LookupError):
    pass


def normalize_label(value: str) -> str:
    """Trim and ASCII-casefold; non-ASCII characters keep exact form for precise matching."""
    return value.strip().casefold()


def custom_ingredient_key(normalized_name: str) -> str:
    digest = sha256(normalized_name.encode("utf-8")).hexdigest()[:24]
    return f"custom_{digest}"


def custom_ingredient_id(normalized_name: str) -> str:
    return str(uuid5(_CUSTOM_NS, f"mealflow.ingredient:{normalized_name}"))


def ensure_preset_aliases(session: Session) -> int:
    """Idempotently insert preset aliases for ingredients that already exist."""
    created = 0
    canonical = {
        normalize_label(row.name): row.id for row in session.scalars(select(Ingredient)).all()
    }
    for key, aliases in PRESET_ALIASES.items():
        ingredient = session.scalar(select(Ingredient).where(Ingredient.key == key))
        if ingredient is None:
            continue
        for alias in aliases:
            normalized = normalize_label(alias)
            if not normalized:
                continue
            owner = canonical.get(normalized)
            if owner is not None and owner != ingredient.id:
                continue
            existing = session.scalar(
                select(IngredientAlias).where(IngredientAlias.normalized_alias == normalized)
            )
            if existing is not None:
                continue
            session.add(
                IngredientAlias(
                    ingredient_id=ingredient.id,
                    alias=alias.strip(),
                    normalized_alias=normalized,
                )
            )
            created += 1
    if created:
        session.flush()
    return created


def _match_by_normalized_name(session: Session, normalized: str) -> Ingredient | None:
    for row in session.scalars(select(Ingredient)).all():
        if normalize_label(row.name) == normalized:
            return row
    alias = session.scalar(
        select(IngredientAlias).where(IngredientAlias.normalized_alias == normalized)
    )
    if alias is None:
        return None
    return session.get(Ingredient, alias.ingredient_id)


def resolve_ingredient(
    session: Session,
    *,
    ingredient_id: str | None = None,
    ingredient_key: str | None = None,
    name: str | None = None,
    category: str | None = None,
    default_unit: str | None = None,
) -> Ingredient:
    """Resolve a catalog ingredient; unknown free-text names get a stable custom row.

    Client-provided ids/keys are validated server-side. ingredient_key is compatibility
    only and is never treated as the stable inventory identity.
    """
    if ingredient_id:
        ingredient = session.get(Ingredient, ingredient_id)
        if ingredient is None:
            raise IngredientNotFoundError(ingredient_id)
        return ingredient

    if ingredient_key:
        key = ingredient_key.strip()
        ingredient = session.scalar(select(Ingredient).where(Ingredient.key == key))
        if ingredient is None:
            raise IngredientNotFoundError(key)
        return ingredient

    label = (name or "").strip()
    if not label:
        raise IngredientNotFoundError("missing ingredient reference")
    normalized = normalize_label(label)
    matched = _match_by_normalized_name(session, normalized)
    if matched is not None:
        return matched

    key = custom_ingredient_key(normalized)
    existing = session.scalar(select(Ingredient).where(Ingredient.key == key))
    if existing is not None:
        return existing

    ingredient = Ingredient(
        id=custom_ingredient_id(normalized),
        key=key,
        name=label,
        category=(category or "其他").strip() or "其他",
        default_unit=(default_unit or "份").strip() or "份",
        price_source="用户自定义食材，暂无参考价",
    )
    session.add(ingredient)
    session.flush()
    return ingredient


def search_ingredients(session: Session, search: str = "", limit: int = 30) -> list[Ingredient]:
    query = select(Ingredient).order_by(Ingredient.category, Ingredient.name).limit(limit)
    term = search.strip()
    if term:
        pattern = f"%{term}%"
        alias_ids = select(IngredientAlias.ingredient_id).where(
            or_(
                IngredientAlias.alias.ilike(pattern),
                IngredientAlias.normalized_alias == normalize_label(term),
            )
        )
        query = (
            select(Ingredient)
            .where(
                or_(
                    Ingredient.name.ilike(pattern),
                    Ingredient.key.ilike(pattern),
                    Ingredient.category.ilike(pattern),
                    Ingredient.id.in_(alias_ids),
                )
            )
            .order_by(Ingredient.category, Ingredient.name)
            .limit(limit)
        )
    return list(session.scalars(query))


def ingredient_view(row: Ingredient, session: Session | None = None) -> dict:
    aliases: list[str] = []
    if session is not None:
        aliases = list(
            session.scalars(
                select(IngredientAlias.alias)
                .where(IngredientAlias.ingredient_id == row.id)
                .order_by(IngredientAlias.alias)
            )
        )
    return {
        "id": row.id,
        "key": row.key,
        "name": row.name,
        "category": row.category,
        "default_unit": row.default_unit,
        "aliases": aliases,
    }
