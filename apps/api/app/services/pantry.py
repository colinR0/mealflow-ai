"""家庭常备料策略。

常备料不是库存：它默认可用、不进入缺货或采购建议，但仍是完整菜谱和
过敏/忌口校验的一部分。这里集中维护该规则，避免聊天与正式配餐各自判断。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import HouseholdPantryConfig


DEFAULT_PANTRY_STAPLE_LABELS = {
    "salt": "盐",
    "sugar": "糖",
    "oil": "食用油",
    "soy_sauce": "生抽",
    "dark_soy_sauce": "老抽",
    "oyster_sauce": "蚝油",
    "vinegar": "醋",
    "starch": "淀粉",
    "peppercorn": "花椒",
    "scallion": "葱",
    "ginger": "姜",
    "garlic": "蒜",
    "cooking_wine": "料酒",
    "pepper": "胡椒",
    "dried_chili": "干辣椒",
    "doubanjiang": "豆瓣酱",
}

DEFAULT_PANTRY_STAPLE_KEYS = frozenset(DEFAULT_PANTRY_STAPLE_LABELS)


def effective_pantry_keys(session: Session | None, workspace_id: str) -> set[str]:
    """返回默认库经家庭覆盖后的有效常备料 key。"""
    config = session.get(HouseholdPantryConfig, workspace_id) if session else None
    if config is None:
        return set(DEFAULT_PANTRY_STAPLE_KEYS)
    keys = set(DEFAULT_PANTRY_STAPLE_KEYS) if config.use_defaults else set()
    keys.update(config.custom_keys)
    keys.difference_update(config.disabled_default_keys)
    return keys


def pantry_label(key: str) -> str:
    """把规则层使用的稳定 key 转成人类可读的中文名称。"""
    return DEFAULT_PANTRY_STAPLE_LABELS.get(key, key)
