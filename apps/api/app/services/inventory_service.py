"""库存服务：库存项的查询与维护（Agent 决策与库存页共用同一数据源）。"""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Ingredient, InventoryItem, Workspace
from app.schemas.inventory import InventoryCreate, InventoryItemView, InventoryUpdate

EXPIRING_WINDOW_DAYS = 2


class WorkspaceNotFoundError(LookupError):
    pass


class ItemNotFoundError(LookupError):
    pass


class IngredientNotFoundError(LookupError):
    pass


class UnitMismatchError(ValueError):
    pass


def _format_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _to_view(session: Session, item: InventoryItem, ingredient: Ingredient) -> InventoryItemView:
    expires_on = item.expires_on
    is_expiring = expires_on is not None and expires_on <= date.today() + timedelta(
        days=EXPIRING_WINDOW_DAYS
    )
    return InventoryItemView(
        id=item.id,
        ingredient_key=ingredient.key,
        ingredient_name=ingredient.name,
        category=ingredient.category,
        quantity=_format_decimal(item.quantity),
        unit=item.unit,
        expires_on=expires_on,
        is_expiring=is_expiring,
    )


def _get_ingredient(session: Session, key: str) -> Ingredient:
    ingredient = session.scalar(select(Ingredient).where(Ingredient.key == key))
    if ingredient is None:
        raise IngredientNotFoundError(f"食材 {key} 不存在")
    return ingredient


def _resolve_ingredient(session: Session, payload: InventoryCreate) -> Ingredient:
    """解析已有食材，或为用户输入创建可被 Agent 识别的自定义食材。"""
    if payload.ingredient_key:
        return _get_ingredient(session, payload.ingredient_key)

    name = payload.ingredient_name or ""
    existing = session.scalar(select(Ingredient).where(Ingredient.name == name))
    if existing is not None:
        return existing

    ingredient = Ingredient(
        key=f"custom_{uuid4().hex}",
        name=name,
        category=payload.category or "其他",
        default_unit=payload.unit or "份",
        reference_price=None,
        price_source="用户自定义食材，暂无参考价",
    )
    session.add(ingredient)
    session.flush()
    return ingredient


def list_items(session: Session, workspace_id: str) -> list[InventoryItemView]:
    if session.get(Workspace, workspace_id) is None:
        raise WorkspaceNotFoundError(f"工作区 {workspace_id} 不存在")
    rows = session.execute(
        select(InventoryItem, Ingredient)
        .join(Ingredient, Ingredient.id == InventoryItem.ingredient_id)
        .where(InventoryItem.workspace_id == workspace_id)
        .order_by(InventoryItem.expires_on.nulls_last(), Ingredient.name)
    ).all()
    return [_to_view(session, item, ingredient) for item, ingredient in rows]


def add_item(session: Session, workspace_id: str, payload: InventoryCreate) -> InventoryItemView:
    """新增库存项；同一食材已存在时按数据字典语义更新为最新数量（upsert）。

    学习路线：先校验工作区 → 解析食材 → 校验单位 → 查询已有库存 →
    新建或更新 → 提交事务 → 转为接口响应。
    """
    # 用主键查询工作区；不存在说明 URL 中传来的 workspace_id 无效。
    if session.get(Workspace, workspace_id) is None:
        # 抛出业务层异常，由 Router 统一翻译为 HTTP 404 响应。
        raise WorkspaceNotFoundError(f"工作区 {workspace_id} 不存在")

    # 根据 Schema 已校验的 payload 找到已有食材，或创建用户输入的自定义食材。
    ingredient = _resolve_ingredient(session, payload)

    # 用户未填写单位时，回退为食材数据字典定义的默认单位。
    unit = payload.unit or ingredient.default_unit

    # Schema 只能校验“单位是合法字符串”；与该食材是否能安全匹配属于业务规则。
    if unit != ingredient.default_unit:
        # 当前项目没有单位换算表，因此不擅自把“克”和“个”等不同单位互相转换。
        raise UnitMismatchError(
            f"单位 {unit} 与食材默认单位 {ingredient.default_unit} 不一致，无法安全换算，需人工确认"
        )

    # 查询“当前工作区 + 当前食材”的库存记录；scalar 取一条记录，无结果时返回 None。
    item = session.scalar(
        # select(InventoryItem) 对应 SQL 的 SELECT ... FROM inventory_items。
        select(InventoryItem).where(
            # 第一项条件：库存必须属于本次请求的工作区。
            InventoryItem.workspace_id == workspace_id,
            # 第二项条件：库存必须对应刚才解析出的食材。
            InventoryItem.ingredient_id == ingredient.id,
        )
    )

    # 找不到记录时才创建；找到时复用旧对象，实现“有则更新、无则新增”。
    if item is None:
        # 此时只创建 Python ORM 对象，并指定两张表之间的外键关联。
        item = InventoryItem(workspace_id=workspace_id, ingredient_id=ingredient.id)
        # 放入当前数据库事务，commit 前尚未永久写入 PostgreSQL。
        session.add(item)

    # 无论新建或已有记录，都把数量覆盖为本次提交的最新值，而不是累加。
    item.quantity = payload.quantity
    # 保存已通过业务规则确认的单位。
    item.unit = unit
    # 保存到期日；payload 中允许为空，表示用户暂时未知到期日。
    item.expires_on = payload.expires_on

    # 提交事务：此时新增或更新才真正持久化。
    session.commit()

    # 将 ORM 对象转换成对外稳定的 Pydantic 响应格式，再返回给 Router 和前端。
    return _to_view(session, item, ingredient)


def update_item(
    session: Session, item_id: str, payload: InventoryUpdate
) -> InventoryItemView:
    item = session.get(InventoryItem, item_id)
    if item is None:
        raise ItemNotFoundError(f"库存项 {item_id} 不存在")
    ingredient = session.get(Ingredient, item.ingredient_id)
    fields = payload.model_fields_set
    if "quantity" in fields and payload.quantity is not None:
        item.quantity = payload.quantity
    if "unit" in fields and payload.unit is not None:
        if ingredient and payload.unit != ingredient.default_unit:
            raise UnitMismatchError(
                f"单位 {payload.unit} 与食材默认单位 {ingredient.default_unit} 不一致，需人工确认"
            )
        item.unit = payload.unit
    if "expires_on" in fields:
        item.expires_on = payload.expires_on
    session.commit()
    return _to_view(session, item, ingredient)


def delete_item(session: Session, item_id: str) -> None:
    item = session.get(InventoryItem, item_id)
    if item is None:
        raise ItemNotFoundError(f"库存项 {item_id} 不存在")
    session.delete(item)
    session.commit()


def list_ingredients(session: Session) -> list[dict[str, str]]:
    rows = session.scalars(select(Ingredient).order_by(Ingredient.category, Ingredient.name)).all()
    return [
        {"key": i.key, "name": i.name, "category": i.category, "default_unit": i.default_unit}
        for i in rows
    ]
