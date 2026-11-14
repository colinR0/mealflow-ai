"""Six tools over research recipes and confirmed household business facts."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import sessionmaker

from app.db.models import Ingredient, IngredientAlias
from app.db.v2_models import HouseholdProfile, InventoryLot, ShoppingItem
from app.services.ingredient_catalog import normalize_label
from app.services.research_retrieval import RecipeRetriever, RetrievalError
from app.services.v2_runtime import authorized_workspace, prepare_action
from app.tools.contracts import Tool, ToolInput, ToolResult, dispatch


class SearchInput(ToolInput):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=5, ge=1, le=20)


class DetailInput(ToolInput):
    document_id: str = Field(min_length=1, max_length=64)
    content_version: str = Field(pattern=r"^[0-9a-f]{64}$")


class PageInput(ToolInput):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)


class InventoryInput(PageInput):
    search: str = Field(default="", max_length=120)


class ProfilesInput(PageInput):
    member_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)
    as_of: date | None = None


class ProcurementInput(PageInput):
    search: str = Field(default="", max_length=120)
    status: Literal["pending", "purchased", "archived"] | None = "pending"


class PrepareInput(ToolInput):
    name: str = Field(min_length=1, max_length=120)
    quantity_status: Literal["unknown", "known"] = "unknown"
    quantity: Decimal | None = Field(
        default=None, ge=0, le=Decimal("999999999.999"), max_digits=12, decimal_places=3
    )
    unit: str | None = Field(default=None, min_length=1, max_length=20)
    note: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def quantity_contract(self):
        if self.quantity_status == "known" and (self.quantity is None or not self.unit):
            raise ValueError("known quantity needs quantity and unit")
        if self.quantity_status == "unknown" and (
            self.quantity is not None or self.unit is not None
        ):
            raise ValueError("unknown quantity must not contain quantity or unit")
        return self


@dataclass(frozen=True)
class ToolContext:
    """Trusted server/developer context, never part of model-visible tool arguments."""

    actor_id: str
    household_id: str
    sessions: sessionmaker
    batch_id: str = ""
    model_dir: Path = Path("models/bge-small-zh-v1.5")


class AssistantTools:
    def __init__(self, context: ToolContext) -> None:
        self.context = context
        self._retriever: RecipeRetriever | None = None
        # One model per toolset; serialize initialization/inference, including after caller timeout.
        self._rag_lock = Lock()
        definitions = [
            (
                "search_recipes",
                "搜索研发菜谱，返回相关片段与来源；不用于查询库存或判断过敏安全",
                SearchInput,
                "read",
                self.search_recipes,
                120,
            ),
            (
                "get_recipe_detail",
                "按搜索返回的文档ID和内容版本读取完整研发菜谱",
                DetailInput,
                "read",
                self.get_recipe_detail,
                10,
            ),
            (
                "get_inventory_summary",
                "查询当前家庭已确认的有效库存，保留未知数量",
                InventoryInput,
                "read",
                self.get_inventory_summary,
                10,
            ),
            (
                "get_member_profiles",
                "查询当前家庭人员、原始饮食限制和指定日期有效事件；不代表已选择用餐人员",
                ProfilesInput,
                "read",
                self.get_member_profiles,
                10,
            ),
            (
                "get_procurement_list",
                "查询当前家庭采购清单；默认待购买，status=null查询全部状态",
                ProcurementInput,
                "read",
                self.get_procurement_list,
                10,
            ),
            (
                "prepare_procurement_action",
                "准备一项采购待确认草稿；不会直接添加采购条目，必须由用户确认",
                PrepareInput,
                "prepare",
                self.prepare_procurement_action,
                10,
            ),
        ]
        self.registry = {
            name: Tool(name, description, schema, effect, self._async(handler), timeout)
            for name, description, schema, effect, handler, timeout in definitions
        }

    @staticmethod
    def _async(handler):
        async def invoke(body):
            # Synchronous SQL/model calls must not block the timeout/cancellation event loop.
            return await asyncio.to_thread(handler, body)

        return invoke

    def declarations(self) -> list[dict]:
        return [tool.declaration() for tool in self.registry.values()]

    async def call(self, name: str, arguments: str) -> ToolResult:
        return await dispatch(self.registry.get(name), arguments)

    @contextmanager
    def _session(self):
        with self.context.sessions() as session, session.begin():
            session.connection().exec_driver_sql(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            )
            session.connection().exec_driver_sql("SET LOCAL statement_timeout = '10s'")
            authorized_workspace(session, self.context.actor_id, self.context.household_id)
            yield session

    def _recipes(self) -> RecipeRetriever:
        if not self.context.batch_id:
            raise RetrievalError("index_config", "尚未配置研发索引批次")
        if self._retriever is None:
            self._retriever = RecipeRetriever(
                engine=self.context.sessions.kw["bind"],
                model_dir=self.context.model_dir,
                batch_id=self.context.batch_id,
            )
        return self._retriever

    def search_recipes(self, body: SearchInput) -> ToolResult:
        with self._session():
            pass
        with self._rag_lock:
            result = self._recipes().search(body.query, top_k=body.top_k, mode="hybrid")
        hits = result["hits"]
        ids = [
            f"research:{self.context.batch_id}:{hit['document_id']}:{hit['content_version']}"
            for hit in hits
        ]
        return ToolResult(
            status="ok" if hits else "no_result",
            data=result,
            evidence_ids=ids,
            data_versions={"batch_id": self.context.batch_id},
            warnings=["研发索引未发布；检索分数不是正确概率，未执行忌口安全过滤"],
        )

    def get_recipe_detail(self, body: DetailInput) -> ToolResult:
        with self._session():
            pass
        with self._rag_lock:
            result = self._recipes().get_detail(body.document_id, body.content_version)
        return ToolResult(
            status="ok" if result["document"] else "no_result",
            data=result,
            evidence_ids=(
                [f"research:{self.context.batch_id}:{body.document_id}:{body.content_version}"]
                if result["document"]
                else []
            ),
            data_versions={"batch_id": self.context.batch_id},
            warnings=["研发菜谱未发布；未知营养、份量和过敏信息保持未知"],
        )

    @staticmethod
    def _page(session, statement, body):
        total = session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        rows = session.scalars(statement.offset(body.offset).limit(body.limit)).all()
        return rows, {"total": total, "offset": body.offset, "limit": body.limit}

    def get_inventory_summary(self, body: InventoryInput) -> ToolResult:
        with self._session() as session:
            statement = select(InventoryLot).where(
                InventoryLot.workspace_id == self.context.household_id,
                InventoryLot.status == "active",
            )
            if body.search:
                alias_ids = select(IngredientAlias.ingredient_id).where(
                    IngredientAlias.alias.icontains(body.search, autoescape=True)
                )
                statement = statement.where(
                    or_(
                        InventoryLot.name.icontains(body.search, autoescape=True),
                        InventoryLot.ingredient_id.in_(alias_ids),
                    )
                )
            rows, page = self._page(session, statement.order_by(InventoryLot.id), body)
            items = [
                {
                    "id": row.id,
                    "name": row.name,
                    "ingredient_id": row.ingredient_id,
                    "ingredient_key": row.ingredient_key,
                    "quantity_status": row.quantity_status,
                    "quantity": str(row.quantity) if row.quantity is not None else None,
                    "unit": row.unit,
                    "expires_on": row.expires_on.isoformat() if row.expires_on else None,
                    "confirmed_at": row.confirmed_at.isoformat(),
                    "version": row.version,
                    "status": row.status,
                }
                for row in rows
            ]
        return ToolResult(
            status="ok" if items else "no_result",
            data={"items": items, **page},
            evidence_ids=[f"lot:{row['id']}:v{row['version']}" for row in items],
            data_versions={row["id"]: row["version"] for row in items},
        )

    def get_member_profiles(self, body: ProfilesInput) -> ToolResult:
        today = body.as_of or datetime.now(ZoneInfo("Asia/Shanghai")).date()
        warnings = []
        with self._session() as session:
            statement = select(HouseholdProfile).where(
                HouseholdProfile.workspace_id == self.context.household_id
            )
            if body.member_ids is not None:
                statement = statement.where(HouseholdProfile.id.in_(body.member_ids))
                found = set(session.scalars(statement.with_only_columns(HouseholdProfile.id)))
                if found != set(body.member_ids):
                    raise HTTPException(404, detail={"code": "PROFILE_NOT_FOUND"})
            rows, page = self._page(session, statement.order_by(HouseholdProfile.id), body)
            items = []
            for row in rows:
                details = row.legacy_details or {}
                active = []
                for event in details.get("events") or []:
                    try:
                        start, end = (
                            date.fromisoformat(event["start_date"]),
                            date.fromisoformat(event["end_date"]),
                        )
                        if end < start:
                            raise ValueError("event dates reversed")
                    except (KeyError, TypeError, ValueError):
                        warnings.append(f"人员 {row.id} 存在无法解析的事件，饮食约束需要核对")
                        continue
                    if start <= today <= end:
                        active.append(event)
                items.append(
                    {
                        "id": row.id,
                        "display_name": row.display_name,
                        "restrictions": row.restrictions,
                        "preferences": row.preferences,
                        "taboo": details.get("taboo", ""),
                        "note": details.get("note", ""),
                        "active_events": active,
                        "version": row.version,
                        "allergy_information": "recorded"
                        if any(
                            isinstance(item, dict) and item.get("type") == "allergy"
                            for item in row.restrictions
                        )
                        else "not_recorded",
                    }
                )
        return ToolResult(
            status="partial" if warnings else ("ok" if items else "no_result"),
            data={"items": items, "as_of": today.isoformat(), **page},
            warnings=[*warnings, "未记录过敏不代表确认无过敏；返回人员不等于已选择用餐人员"],
            evidence_ids=[f"profile:{row['id']}:v{row['version']}" for row in items],
            data_versions={row["id"]: row["version"] for row in items},
        )

    def get_procurement_list(self, body: ProcurementInput) -> ToolResult:
        with self._session() as session:
            statement = select(ShoppingItem).where(
                ShoppingItem.workspace_id == self.context.household_id
            )
            if body.status is not None:
                statement = statement.where(ShoppingItem.status == body.status)
            if body.search:
                statement = statement.where(
                    ShoppingItem.name.icontains(body.search, autoescape=True)
                )
            rows, page = self._page(session, statement.order_by(ShoppingItem.id), body)
            items = [
                {
                    "id": row.id,
                    "name": row.name,
                    "ingredient_key": row.ingredient_key,
                    "quantity_status": row.quantity_status,
                    "quantity": str(row.quantity) if row.quantity is not None else None,
                    "unit": row.unit,
                    "status": row.status,
                    "note": row.note,
                    "version": row.version,
                }
                for row in rows
            ]
        return ToolResult(
            status="ok" if items else "no_result",
            data={"items": items, **page},
            evidence_ids=[f"shopping:{row['id']}:v{row['version']}" for row in items],
            data_versions={row["id"]: row["version"] for row in items},
        )

    def prepare_procurement_action(self, body: PrepareInput) -> ToolResult:
        with self._session() as session:
            normalized = normalize_label(body.name)
            alias_ids = select(IngredientAlias.ingredient_id).where(
                IngredientAlias.normalized_alias == normalized
            )
            matches = session.scalars(
                select(Ingredient).where(
                    or_(func.lower(Ingredient.name) == normalized, Ingredient.id.in_(alias_ids))
                )
            ).all()
            # Exact unique matches only; resolving an unknown name must not create catalog rows.
            key = matches[0].key if len(matches) == 1 else "unknown"
        payload = body.model_dump(mode="json")
        payload["ingredient_key"] = key
        action = prepare_action(
            self.context.actor_id,
            self.context.household_id,
            "add_procurement",
            payload,
            sessions=self.context.sessions,
        )
        return ToolResult(
            status="ok",
            data={"pending_action": action, "requires_confirmation": True},
            warnings=[] if key != "unknown" else ["该食材尚未唯一关联目录，保留原始名称供用户确认"],
            data_versions={"action": action["version"]},
        )
