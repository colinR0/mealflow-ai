"""规划上下文加载:Agent 感知工具的数据来源。

家庭厨房版:约束前置 —— 成员档案(永久忌口/过敏)+ 特殊事件(带生效期)
在每轮规划前加载,确定性工具保持无副作用。
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    DietaryRestriction,
    Ingredient,
    InventoryItem,
    MealService,
    MemberEvent,
    MemberFoodPreference,
    Recipe,
    RecipeIngredient,
    Site,
    User,
    Workspace,
    WorkspaceMember,
)
from app.services.pantry import effective_pantry_keys
from app.services.recipe_taxonomy import IngredientFact, classify_recipe

EXPIRING_WINDOW_DAYS = 2

RESTRICTION_LABELS = {"allergy": "过敏", "dislike": "忌口"}


class ScopeNotFoundError(LookupError):
    """请求中的 workspace/site/meal_service 作用域在库中不存在。"""


@dataclass
class InventoryLine:
    key: str
    name: str
    quantity: Decimal
    unit: str
    expires_on: date | None

    def is_expiring(self, today: date) -> bool:
        return (
            self.expires_on is not None
            and self.expires_on <= today + timedelta(days=EXPIRING_WINDOW_DAYS)
        )


@dataclass
class RecipeIngredientLine:
    key: str
    name: str
    quantity: Decimal
    unit: str
    optional: bool
    category: str = ""


@dataclass
class RecipeContext:
    id: str
    name: str
    servings: int
    duration_minutes: int
    kcal_per_serving: int | None
    estimated_cost_cents: int | None
    tags: list[str]
    taste_tags: list[str]
    allergens: list[str]
    steps: list[str] = field(default_factory=list)
    ingredients: list[RecipeIngredientLine] = field(default_factory=list)
    meal_role: str = ""
    staple_type: str = "无"
    protein_sources: list[str] = field(default_factory=list)
    vegetable_keys: list[str] = field(default_factory=list)
    portion_confidence: str = "servings_only"
    source: str = "local"

    @property
    def required_ingredient_keys(self) -> set[str]:
        return {line.key for line in self.ingredients if not line.optional}


@dataclass
class MemberProfile:
    """家庭成员。"""

    user_id: str
    display_name: str


@dataclass
class FoodPreferenceContext:
    user_id: str
    target_type: str
    target_key: str
    target_name: str
    preference: str


@dataclass
class MemberConstraint:
    """一条生效中的成员约束:永久忌口/过敏,或特殊事件派生的口味禁忌。"""

    user_id: str
    member_name: str
    kind: str  # "restriction" | "event"
    level: str  # "forbidden"(过敏,硬过滤) | "warn"(不建议)
    summary: str  # 人话描述,如 "对象·忌口香菜" / "对象·例假期(忌辛辣、忌生冷)"
    ingredient_key: str | None = None  # restriction:受限食材 key
    avoid_tags: list[str] = field(default_factory=list)  # event:冲突口味,如 ["辛辣","油腻"]


@dataclass
class PlanningContext:
    workspace_id: str
    workspace_name: str
    site_name: str
    meal_service_id: str
    meal_service_name: str
    service_date: date
    expected_diners: int
    budget_limit_cents: int | None
    inventory: list[InventoryLine]
    recipes: list[RecipeContext]
    members: list[MemberProfile]
    member_constraints: list[MemberConstraint]
    excluded_keys: list[str]
    # 数据字典:用餐限制区分 allergy / dislike,均进入硬过滤;用于解释来源
    restriction_types: dict[str, str] = field(default_factory=dict)
    dish_feedback: dict[str, "DishFeedbackStats"] = field(default_factory=dict)
    pantry_keys: set[str] = field(default_factory=set)
    food_preferences: list[FoodPreferenceContext] = field(default_factory=list)
    ingredient_names: dict[str, str] = field(default_factory=dict)

    def expiring_keys(self, today: date) -> set[str]:
        return {line.key for line in self.inventory if line.is_expiring(today)}

    def available_for(self, ingredient_key: str) -> Decimal:
        for line in self.inventory:
            if line.key == ingredient_key:
                return line.quantity
        return Decimal("0")

    def inventory_unit(self, ingredient_key: str) -> str | None:
        for line in self.inventory:
            if line.key == ingredient_key:
                return line.unit
        return None

    def active_event_summaries(self) -> list[str]:
        return [c.summary for c in self.member_constraints if c.kind == "event"]


@dataclass
class DishFeedbackStats:
    """单道菜的历史反馈聚合(预留:家庭版可通过对话记录点赞/踩)。"""

    avg_rating: float
    sample_count: int
    top_tags: list[str] = field(default_factory=list)


def load_planning_context(
    session: Session, workspace_id: str, site_id: str, meal_service_id: str
) -> PlanningContext:
    # 先逐级验证工作区 → 地点 → 餐次的归属关系，再读取数据。
    # 这是 Agent 可相信的边界：不能仅凭前端传来的 ID 跨工作区读取家庭数据。
    workspace = session.get(Workspace, workspace_id)
    if workspace is None:
        raise ScopeNotFoundError(f"workspace {workspace_id} 不存在")
    site = session.get(Site, site_id)
    if site is None or site.workspace_id != workspace.id:
        raise ScopeNotFoundError(f"site {site_id} 不存在或不属于该工作区")
    meal_service = session.get(MealService, meal_service_id)
    if meal_service is None or meal_service.site_id != site.id:
        raise ScopeNotFoundError(f"meal_service {meal_service_id} 不存在或不属于该站点")
    service_date = meal_service.service_date

    # 家庭成员
    member_rows = session.execute(
        select(User, WorkspaceMember)
        .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
        .where(WorkspaceMember.workspace_id == workspace.id)
    ).all()
    members = [
        MemberProfile(user_id=user.id, display_name=user.display_name)
        for user, _member in member_rows
    ]
    member_names = {member.user_id: member.display_name for member in members}

    # 库存
    inventory: list[InventoryLine] = []
    rows = session.execute(
        select(InventoryItem, Ingredient)
        .join(Ingredient, Ingredient.id == InventoryItem.ingredient_id)
        .where(InventoryItem.workspace_id == workspace.id)
    ).all()
    for item, ingredient in rows:
        if item.quantity <= 0:
            continue
        inventory.append(
            InventoryLine(
                key=ingredient.key,
                name=ingredient.name,
                quantity=item.quantity,
                unit=item.unit,
                expires_on=item.expires_on,
            )
        )

    # 菜谱与配方明细。这里组装成脱离 ORM 的 RecipeContext，后续规划层只读它，
    # 因而不会在菜单计算过程中意外触发数据库写入。
    ingredient_names = {
        ingredient.key: ingredient.name
        for ingredient in session.scalars(select(Ingredient)).all()
    }
    recipes: list[RecipeContext] = []
    recipe_rows = session.scalars(select(Recipe)).all()
    links = session.execute(
        select(RecipeIngredient, Ingredient)
        .join(Ingredient, Ingredient.id == RecipeIngredient.ingredient_id)
        .order_by(RecipeIngredient.recipe_id)
    ).all()
    ingredients_by_recipe: dict[str, list[RecipeIngredientLine]] = {}
    for link, ingredient in links:
        ingredients_by_recipe.setdefault(link.recipe_id, []).append(
            RecipeIngredientLine(
                key=ingredient.key,
                name=ingredient.name,
                quantity=link.quantity,
                unit=link.unit,
                optional=link.optional,
                category=ingredient.category,
            )
        )
    for recipe in recipe_rows:
        lines = ingredients_by_recipe.get(recipe.id, [])
        if recipe.meal_role.strip():
            meal_role = recipe.meal_role
            staple_type = recipe.staple_type or "无"
            protein_sources = list(recipe.protein_sources)
            vegetable_keys = list(recipe.vegetable_keys)
            portion_confidence = recipe.portion_confidence or "servings_only"
        else:
            classified = classify_recipe(
                recipe.name,
                list(recipe.tags),
                [
                    IngredientFact(
                        key=line.key,
                        name=line.name,
                        category=line.category,
                        quantity=line.quantity,
                        unit=line.unit,
                        optional=line.optional,
                    )
                    for line in lines
                ],
            )
            meal_role = classified.meal_role
            staple_type = classified.staple_type
            protein_sources = classified.protein_sources
            vegetable_keys = classified.vegetable_keys
            portion_confidence = classified.portion_confidence
        recipes.append(
            RecipeContext(
                id=recipe.id,
                name=recipe.name,
                servings=recipe.servings,
                duration_minutes=recipe.duration_minutes,
                kcal_per_serving=recipe.kcal_per_serving,
                estimated_cost_cents=recipe.estimated_cost_cents,
                tags=recipe.tags,
                taste_tags=recipe.taste_tags,
                allergens=recipe.allergens,
                steps=recipe.steps,
                ingredients=lines,
                meal_role=meal_role,
                staple_type=staple_type,
                protein_sources=protein_sources,
                vegetable_keys=vegetable_keys,
                portion_confidence=portion_confidence,
            )
        )

    # 约束前置:永久忌口/过敏(成员级或全家共享)+ 当日生效的特殊事件
    member_constraints: list[MemberConstraint] = []
    excluded_keys: list[str] = []
    restriction_types: dict[str, str] = {}
    restriction_rows = session.execute(
        select(DietaryRestriction, User)
        .join(User, User.id == DietaryRestriction.user_id, isouter=True)
        .where(DietaryRestriction.workspace_id == workspace.id)
    ).all()
    for restriction, user in restriction_rows:
        member_name = user.display_name if user else "全家"
        label = RESTRICTION_LABELS.get(restriction.type, restriction.type)
        summary = f"{member_name}·{label}{restriction.ingredient_key}"
        level = "forbidden" if restriction.type == "allergy" else "warn"
        member_constraints.append(
            MemberConstraint(
                user_id=restriction.user_id or "",
                member_name=member_name,
                kind="restriction",
                level=level,
                summary=summary,
                ingredient_key=restriction.ingredient_key,
            )
        )
        excluded_keys.append(restriction.ingredient_key)
        restriction_types[restriction.ingredient_key] = restriction.type

    event_rows = session.execute(
        select(MemberEvent, User)
        .join(User, User.id == MemberEvent.user_id)
        .where(
            MemberEvent.workspace_id == workspace.id,
            MemberEvent.start_date <= service_date,
            MemberEvent.end_date >= service_date,
        )
    ).all()
    for event, user in event_rows:
        member_name = user.display_name if user else "家庭成员"
        avoid_tags = [tag.removeprefix("忌") for tag in event.constraint_tags if tag.startswith("忌")]
        prefer_tags = [tag.removeprefix("宜") for tag in event.constraint_tags if tag.startswith("宜")]
        detail = "、".join(event.constraint_tags)
        member_constraints.append(
            MemberConstraint(
                user_id=event.user_id,
                member_name=member_name,
                kind="event",
                level="warn",
                summary=f"{member_name}·{event.event_type}({detail})",
                avoid_tags=avoid_tags,
            )
        )
        _ = prefer_tags  # 预留:宜XX作为加分项进入评分

    preference_rows = session.scalars(
        select(MemberFoodPreference).where(MemberFoodPreference.workspace_id == workspace.id)
    ).all()

    return PlanningContext(
        workspace_id=workspace.id,
        workspace_name=workspace.name,
        site_name=site.name,
        meal_service_id=meal_service.id,
        meal_service_name=meal_service.name,
        service_date=service_date,
        expected_diners=meal_service.expected_diners,
        budget_limit_cents=meal_service.budget_limit_cents,
        inventory=inventory,
        recipes=recipes,
        members=members,
        member_constraints=member_constraints,
        excluded_keys=excluded_keys,
        restriction_types=restriction_types,
        pantry_keys=effective_pantry_keys(session, workspace.id),
        ingredient_names=ingredient_names,
        food_preferences=[
            FoodPreferenceContext(
                user_id=item.user_id,
                target_type=item.target_type,
                target_key=item.target_key,
                target_name=item.target_name,
                preference=item.preference,
            )
            for item in preference_rows
        ],
    )
