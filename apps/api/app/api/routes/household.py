"""家庭档案接口:成员、永久忌口/过敏、特殊事件(带生效期)与食谱详情。"""

from datetime import date
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    DietaryRestriction,
    HouseholdPantryConfig,
    Ingredient,
    InventoryItem,
    MemberEvent,
    MemberFoodPreference,
    Recipe,
    RecipeIngredient,
    User,
    WorkspaceMember,
)
from app.db.session import SessionLocal
from app.services.pantry import DEFAULT_PANTRY_STAPLE_KEYS, effective_pantry_keys, pantry_label

router = APIRouter(tags=["household"])

# 特殊事件类型 → 默认约束 tags(可在创建时覆盖);忌X 参与冲突检测,宜X 作为偏好
EVENT_DEFAULT_TAGS: dict[str, list[str]] = {
    "例假": ["忌辛辣", "忌生冷"],
    "感冒": ["忌辛辣", "忌油腻", "宜清淡"],
    "肠胃不适": ["忌油腻", "忌生冷", "宜清淡"],
    "康复期": ["宜清淡", "宜温补"],
    "最近在健身": ["宜高蛋白", "忌油炸", "宜清淡"],
    "减脂": ["忌油腻", "忌油炸", "宜清淡", "宜低卡"],
    "其他": [],
}


class RestrictionView(BaseModel):
    id: str
    user_id: str | None
    type: str
    ingredient_key: str
    ingredient_name: str


class MemberEventView(BaseModel):
    id: str
    user_id: str
    member_name: str
    event_type: str
    constraint_tags: list[str]
    start_date: date
    end_date: date
    note: str | None


class FoodPreferenceView(BaseModel):
    id: str
    user_id: str
    target_type: str
    target_key: str
    target_name: str
    preference: str
    source: str


class MemberView(BaseModel):
    user_id: str
    display_name: str
    role: str
    gender: str | None
    residency: str
    taboo: str | None
    note: str | None
    restrictions: list[RestrictionView]
    food_preferences: list[FoodPreferenceView] = Field(default_factory=list)


class HouseholdView(BaseModel):
    workspace_id: str
    members: list[MemberView]
    events: list[MemberEventView]


class PantryItemView(BaseModel):
    key: str
    label: str
    is_custom: bool


class PantryConfigView(BaseModel):
    workspace_id: str
    use_defaults: bool
    disabled_default_keys: list[str]
    custom_keys: list[str]
    effective_keys: list[str]
    effective_items: list[PantryItemView]


class PantryConfigUpdateRequest(BaseModel):
    use_defaults: bool = True
    disabled_default_keys: list[str] = Field(default_factory=list)
    custom_keys: list[str] = Field(default_factory=list)


def _pantry_config_view(
    session: Session, workspace_id: str, config: HouseholdPantryConfig | None
) -> PantryConfigView:
    custom_keys = [] if config is None else config.custom_keys
    effective_keys = effective_pantry_keys(session, workspace_id)
    custom_set = set(custom_keys)
    items = sorted(
        (
            PantryItemView(
                key=key,
                label=pantry_label(key),
                is_custom=key in custom_set,
            )
            for key in effective_keys
        ),
        key=lambda item: (item.is_custom, item.label),
    )
    return PantryConfigView(
        workspace_id=workspace_id,
        use_defaults=True if config is None else config.use_defaults,
        disabled_default_keys=[] if config is None else config.disabled_default_keys,
        custom_keys=custom_keys,
        effective_keys=sorted(effective_keys),
        effective_items=items,
    )


class EventCreateRequest(BaseModel):
    workspace_id: str = "home-workspace"
    user_id: str
    event_type: str = Field(min_length=1, max_length=32)
    constraint_tags: list[str] = Field(default_factory=list)
    start_date: date
    end_date: date
    note: str | None = Field(default=None, max_length=200)


class EventUpdateRequest(BaseModel):
    event_type: str | None = None
    constraint_tags: list[str] | None = None
    start_date: date | None = None
    end_date: date | None = None
    note: str | None = None


class MemberCreateRequest(BaseModel):
    workspace_id: str = "home-workspace"
    display_name: str = Field(min_length=1, max_length=40)
    gender: str | None = Field(default=None, max_length=10)
    residency: str = Field(default="resident", pattern="^(resident|guest)$")
    taboo: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=300)


class MemberUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=40)
    gender: str | None = Field(default=None, max_length=10)
    residency: str | None = Field(default=None, pattern="^(resident|guest)$")
    taboo: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=300)


class RestrictionCreateRequest(BaseModel):
    workspace_id: str = "home-workspace"
    user_id: str
    type: str = Field(pattern="^(allergy|dislike)$")
    ingredient_key: str


class FoodPreferenceCreateRequest(BaseModel):
    workspace_id: str = "home-workspace"
    user_id: str
    target_type: str = Field(pattern="^(dish|ingredient)$")
    target_key: str = Field(min_length=1, max_length=160)
    target_name: str = Field(min_length=1, max_length=160)
    preference: str = Field(pattern="^(like|dislike)$")
    source: str = Field(default="profile", pattern="^(profile|conversation)$")


class RecipeIngredientDetail(BaseModel):
    key: str
    name: str
    quantity: float
    unit: str
    optional: bool
    available: float | None = None
    reference_price: float | None = None
    price_source: str | None = None


class RecipeDetailView(BaseModel):
    id: str
    name: str
    servings: int
    duration_minutes: int
    kcal_per_serving: int | None
    estimated_cost_cents: int | None
    tags: list[str]
    taste_tags: list[str]
    allergens: list[str]
    steps: list[str]
    ingredients: list[RecipeIngredientDetail]
    source_name: str


@router.get("/household/pantry/{workspace_id}", response_model=PantryConfigView)
def get_pantry_config(workspace_id: str) -> PantryConfigView:
    with SessionLocal() as session:
        config = session.get(HouseholdPantryConfig, workspace_id)
        return _pantry_config_view(session, workspace_id, config)


@router.put("/household/pantry/{workspace_id}", response_model=PantryConfigView)
def update_pantry_config(workspace_id: str, payload: PantryConfigUpdateRequest) -> PantryConfigView:
    with SessionLocal() as session:
        config = session.get(HouseholdPantryConfig, workspace_id)
        if config is None:
            config = HouseholdPantryConfig(workspace_id=workspace_id)
            session.add(config)
        config.use_defaults = payload.use_defaults
        config.disabled_default_keys = sorted(set(payload.disabled_default_keys) & set(DEFAULT_PANTRY_STAPLE_KEYS))
        config.custom_keys = sorted({item.strip() for item in payload.custom_keys if item.strip()})
        session.commit()
        return _pantry_config_view(session, workspace_id, config)


def _default_tags(event_type: str, tags: list[str]) -> list[str]:
    return tags or EVENT_DEFAULT_TAGS.get(event_type, [])


def _load_household(session: Session, workspace_id: str) -> HouseholdView:
    member_rows = session.execute(
        select(User, WorkspaceMember)
        .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
        .where(WorkspaceMember.workspace_id == workspace_id)
    ).all()
    members_meta = {user.id: (user.display_name, wm.role) for user, wm in member_rows}

    restriction_rows = session.scalars(
        select(DietaryRestriction).where(DietaryRestriction.workspace_id == workspace_id)
    ).all()
    ingredient_names = {
        item.key: item.name
        for item in session.scalars(
            select(Ingredient).where(
                Ingredient.key.in_({r.ingredient_key for r in restriction_rows} or {"-"})
            )
        ).all()
    }
    restrictions_by_user: dict[str | None, list[RestrictionView]] = {}
    for restriction in restriction_rows:
        restrictions_by_user.setdefault(restriction.user_id, []).append(
            RestrictionView(
                id=restriction.id,
                user_id=restriction.user_id,
                type=restriction.type,
                ingredient_key=restriction.ingredient_key,
                ingredient_name=ingredient_names.get(restriction.ingredient_key, restriction.ingredient_key),
            )
        )

    preference_rows = session.scalars(
        select(MemberFoodPreference).where(MemberFoodPreference.workspace_id == workspace_id)
    ).all()
    preferences_by_user: dict[str, list[FoodPreferenceView]] = {}
    for preference in preference_rows:
        preferences_by_user.setdefault(preference.user_id, []).append(
            FoodPreferenceView(
                id=preference.id,
                user_id=preference.user_id,
                target_type=preference.target_type,
                target_key=preference.target_key,
                target_name=preference.target_name,
                preference=preference.preference,
                source=preference.source,
            )
        )

    user_rows = {
        user.id: user
        for user in session.scalars(
            select(User).where(User.id.in_(set(members_meta) or {"-"}))
        ).all()
    }
    members = [
        MemberView(
            user_id=user_id,
            display_name=display_name,
            role=role,
            gender=user_rows[user_id].gender,
            residency=user_rows[user_id].residency,
            taboo=user_rows[user_id].taboo,
            note=user_rows[user_id].note,
            restrictions=restrictions_by_user.get(user_id, []),
            food_preferences=preferences_by_user.get(user_id, []),
        )
        for user_id, (display_name, role) in members_meta.items()
    ]

    event_rows = session.scalars(
        select(MemberEvent)
        .where(MemberEvent.workspace_id == workspace_id)
        .order_by(MemberEvent.start_date)
    ).all()
    events = [
        MemberEventView(
            id=event.id,
            user_id=event.user_id,
            member_name=members_meta.get(event.user_id, ("未知成员", ""))[0],
            event_type=event.event_type,
            constraint_tags=event.constraint_tags,
            start_date=event.start_date,
            end_date=event.end_date,
            note=event.note,
        )
        for event in event_rows
    ]
    return HouseholdView(workspace_id=workspace_id, members=members, events=events)


@router.get("/household", response_model=HouseholdView)
def get_household(workspace_id: str = "home-workspace") -> HouseholdView:
    with SessionLocal() as session:
        return _load_household(session, workspace_id)


@router.post(
    "/household/events",
    response_model=HouseholdView,
    status_code=status.HTTP_201_CREATED,
)
def create_event(payload: EventCreateRequest) -> HouseholdView:
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=400, detail="结束日期不能早于开始日期")
    with SessionLocal() as session:
        member = session.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == payload.workspace_id,
                WorkspaceMember.user_id == payload.user_id,
            )
        )
        if member is None:
            raise HTTPException(status_code=404, detail="家庭成员不存在")
        session.add(
            MemberEvent(
                id=str(uuid4()),
                workspace_id=payload.workspace_id,
                user_id=payload.user_id,
                event_type=payload.event_type,
                constraint_tags=_default_tags(payload.event_type, payload.constraint_tags),
                start_date=payload.start_date,
                end_date=payload.end_date,
                note=payload.note,
            )
        )
        session.commit()
        return _load_household(session, payload.workspace_id)


@router.patch("/household/events/{event_id}", response_model=HouseholdView)
def update_event(event_id: str, payload: EventUpdateRequest) -> HouseholdView:
    with SessionLocal() as session:
        event = session.get(MemberEvent, event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="事件不存在")
        if payload.event_type is not None:
            event.event_type = payload.event_type
        if payload.constraint_tags is not None:
            event.constraint_tags = payload.constraint_tags
        if payload.start_date is not None:
            event.start_date = payload.start_date
        if payload.end_date is not None:
            event.end_date = payload.end_date
        if payload.note is not None:
            event.note = payload.note
        if event.end_date < event.start_date:
            raise HTTPException(status_code=400, detail="结束日期不能早于开始日期")
        session.commit()
        return _load_household(session, event.workspace_id)


@router.delete("/household/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_event(event_id: str) -> None:
    with SessionLocal() as session:
        event = session.get(MemberEvent, event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="事件不存在")
        session.delete(event)
        session.commit()


@router.post(
    "/household/restrictions",
    response_model=HouseholdView,
    status_code=status.HTTP_201_CREATED,
)
def create_restriction(payload: RestrictionCreateRequest) -> HouseholdView:
    with SessionLocal() as session:
        ingredient = session.scalar(
            select(Ingredient).where(Ingredient.key == payload.ingredient_key)
        )
        if ingredient is None:
            raise HTTPException(status_code=404, detail="食材不存在")
        member = session.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == payload.workspace_id,
                WorkspaceMember.user_id == payload.user_id,
            )
        )
        if member is None:
            raise HTTPException(status_code=404, detail="家庭成员不存在")
        exists = session.scalar(
            select(DietaryRestriction).where(
                DietaryRestriction.workspace_id == payload.workspace_id,
                DietaryRestriction.user_id == payload.user_id,
                DietaryRestriction.type == payload.type,
                DietaryRestriction.ingredient_key == payload.ingredient_key,
            )
        )
        if exists is None:
            session.add(
                DietaryRestriction(
                    workspace_id=payload.workspace_id,
                    user_id=payload.user_id,
                    type=payload.type,
                    ingredient_key=payload.ingredient_key,
                )
            )
            session.commit()
        return _load_household(session, payload.workspace_id)


@router.delete(
    "/household/restrictions/{restriction_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_restriction(restriction_id: str) -> None:
    with SessionLocal() as session:
        restriction = session.get(DietaryRestriction, restriction_id)
        if restriction is None:
            raise HTTPException(status_code=404, detail="限制不存在")
        session.delete(restriction)
        session.commit()


@router.post(
    "/household/preferences",
    response_model=HouseholdView,
    status_code=status.HTTP_201_CREATED,
)
def create_food_preference(payload: FoodPreferenceCreateRequest) -> HouseholdView:
    with SessionLocal() as session:
        member = session.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == payload.workspace_id,
                WorkspaceMember.user_id == payload.user_id,
            )
        )
        if member is None:
            raise HTTPException(status_code=404, detail="家庭成员不存在")
        preference = session.scalar(
            select(MemberFoodPreference).where(
                MemberFoodPreference.workspace_id == payload.workspace_id,
                MemberFoodPreference.user_id == payload.user_id,
                MemberFoodPreference.target_type == payload.target_type,
                MemberFoodPreference.target_key == payload.target_key,
            )
        )
        if preference is None:
            preference = MemberFoodPreference(
                workspace_id=payload.workspace_id,
                user_id=payload.user_id,
                target_type=payload.target_type,
                target_key=payload.target_key,
                target_name=payload.target_name,
                preference=payload.preference,
                source=payload.source,
            )
            session.add(preference)
        else:
            preference.target_name = payload.target_name
            preference.preference = payload.preference
            preference.source = payload.source
        session.commit()
        return _load_household(session, payload.workspace_id)


@router.delete("/household/preferences/{preference_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_food_preference(preference_id: str) -> None:
    with SessionLocal() as session:
        preference = session.get(MemberFoodPreference, preference_id)
        if preference is None:
            raise HTTPException(status_code=404, detail="偏好不存在")
        session.delete(preference)
        session.commit()


@router.get("/recipes/{recipe_id}", response_model=RecipeDetailView)
def get_recipe_detail(recipe_id: str, diners: int | None = None) -> RecipeDetailView:
    """食谱详情:用量按用餐人数换算(默认按菜谱基准份数),库存与参考价一并返回。"""
    with SessionLocal() as session:
        recipe = session.get(Recipe, recipe_id)
        if recipe is None:
            raise HTTPException(status_code=404, detail="菜谱不存在")
        scale_denominator = max(recipe.servings, 1)
        scale = (diners or recipe.servings) / scale_denominator

        rows = session.execute(
            select(RecipeIngredient, Ingredient)
            .join(Ingredient, Ingredient.id == RecipeIngredient.ingredient_id)
            .where(RecipeIngredient.recipe_id == recipe.id)
        ).all()
        inventory_quantities: dict[str, float] = {}
        inventory_rows = session.execute(
            select(InventoryItem, Ingredient)
            .join(Ingredient, Ingredient.id == InventoryItem.ingredient_id)
        ).all()
        for item, ingredient in inventory_rows:
            if item.workspace_id == "home-workspace":
                inventory_quantities[ingredient.key] = float(item.quantity)

        ingredients_detail = [
            RecipeIngredientDetail(
                key=ingredient.key,
                name=ingredient.name,
                quantity=round(float(link.quantity) * scale, 3),
                unit=link.unit,
                optional=link.optional,
                available=inventory_quantities.get(ingredient.key),
                reference_price=(
                    float(ingredient.reference_price) if ingredient.reference_price else None
                ),
                price_source=ingredient.price_source,
            )
            for link, ingredient in rows
        ]
        return RecipeDetailView(
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
            ingredients=ingredients_detail,
            source_name=recipe.source_name,
        )


@router.post(
    "/household/members",
    response_model=HouseholdView,
    status_code=status.HTTP_201_CREATED,
)
def create_member(payload: MemberCreateRequest) -> HouseholdView:
    """新增家庭成员(常驻/访客),忌口与备注为自由文本档案。"""
    import uuid

    with SessionLocal() as session:
        exists = session.scalar(
            select(User).where(User.display_name == payload.display_name)
        )
        if exists is not None:
            raise HTTPException(status_code=400, detail="同名成员已存在")
        user = User(
            id=str(uuid.uuid4()),
            email=f"member-{uuid.uuid4().hex[:10]}@home.demo",
            password_hash="not-set",
            display_name=payload.display_name,
            gender=payload.gender,
            residency=payload.residency,
            taboo=payload.taboo,
            note=payload.note,
        )
        session.add(user)
        session.flush()
        session.add(
            WorkspaceMember(
                workspace_id=payload.workspace_id, user_id=user.id, role="user"
            )
        )
        session.commit()
        return _load_household(session, payload.workspace_id)


@router.patch("/household/members/{user_id}", response_model=HouseholdView)
def update_member(user_id: str, payload: MemberUpdateRequest) -> HouseholdView:
    with SessionLocal() as session:
        user = session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="成员不存在")
        fields = payload.model_fields_set
        if "display_name" in fields and payload.display_name is not None:
            user.display_name = payload.display_name
        if "gender" in fields:
            user.gender = payload.gender
        if "residency" in fields and payload.residency is not None:
            user.residency = payload.residency
        if "taboo" in fields:
            user.taboo = payload.taboo
        if "note" in fields:
            user.note = payload.note
        session.commit()
        workspace_id = session.scalar(
            select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user_id)
        )
        return _load_household(session, workspace_id or "home-workspace")


@router.delete(
    "/household/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_member(user_id: str) -> None:
    """删除成员及其事件/限制(主用户不可删,保证 Agent 默认作用域有效)。"""
    with SessionLocal() as session:
        user = session.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="成员不存在")
        if user.id in ("home-me", "home-partner"):
            raise HTTPException(status_code=400, detail="演示主成员不可删除")
        session.query(MemberEvent).filter(MemberEvent.user_id == user_id).delete()
        session.query(DietaryRestriction).filter(
            DietaryRestriction.user_id == user_id
        ).delete()
        session.query(MemberFoodPreference).filter(
            MemberFoodPreference.user_id == user_id
        ).delete()
        session.query(WorkspaceMember).filter(WorkspaceMember.user_id == user_id).delete()
        session.delete(user)
        session.commit()
