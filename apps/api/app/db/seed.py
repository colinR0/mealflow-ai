"""种子数据脚本:预置家庭厨房演示数据(V2 家庭厨房主线)。

- 家庭工作区("我家")、两名成员(我 / 对象)、成员永久忌口与过敏
- 特殊事件:我感冒中(忌辛辣油腻)、对象例假期(忌辛辣生冷),按日期自动生效/过期
- 冰箱库存(家庭单位,含临期食材)、食材主数据带市区大型超市参考价
- data/seed/recipes.json 的可溯源菜谱(含口味 taste_tags,用于冲突检测)

幂等:按天然键 upsert,可重复执行。运行:cd apps/api && python -m app.db.seed
"""

import hashlib
import json
import secrets
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    DietaryRestriction,
    Ingredient,
    InventoryItem,
    MealService,
    MemberEvent,
    Recipe,
    RecipeIngredient,
    Site,
    User,
    Workspace,
    WorkspaceMember,
)
from app.db.session import SessionLocal
from app.services.recipe_taxonomy import IngredientFact, classify_recipe

RECIPE_SEED_PATH = Path(__file__).resolve().parents[4] / "data" / "seed" / "recipes.json"

HOME_WORKSPACE_ID = "home-workspace"
HOME_SITE_ID = "home-kitchen"
HOME_MEAL_SERVICE_ID = "home-lunch"
ME_USER_ID = "home-me"
PARTNER_USER_ID = "home-partner"
GUEST_USER_ID = "home-guest"

# (key, 名称, 类别, 默认单位, 市区大型超市参考价 元/单位)
INGREDIENTS: list[tuple[str, str, str, str, str]] = [
    ("tomato", "番茄", "蔬菜", "kg", "6.50"),
    ("potato", "土豆", "蔬菜", "kg", "3.50"),
    ("greens", "青菜", "蔬菜", "kg", "4.00"),
    ("green_beans", "豆角", "蔬菜", "kg", "9.00"),
    ("pepper_green", "青椒", "蔬菜", "kg", "5.00"),
    ("carrot", "胡萝卜", "蔬菜", "kg", "4.00"),
    ("onion", "洋葱", "蔬菜", "kg", "4.00"),
    ("mushroom", "香菇", "蔬菜", "kg", "12.00"),
    ("cucumber", "黄瓜", "蔬菜", "kg", "4.50"),
    ("eggplant", "茄子", "蔬菜", "kg", "6.00"),
    ("winter_melon", "冬瓜", "蔬菜", "kg", "3.00"),
    ("cilantro", "香菜", "蔬菜", "kg", "8.00"),
    ("garlic", "大蒜", "蔬菜", "kg", "10.00"),
    ("ginger", "生姜", "蔬菜", "kg", "12.00"),
    ("scallion", "小葱", "蔬菜", "kg", "8.00"),
    ("pork", "猪前腿肉", "肉禽", "kg", "22.00"),
    ("chicken_breast", "鸡胸肉", "肉禽", "kg", "16.00"),
    ("chicken_leg", "鸡腿", "肉禽", "kg", "20.00"),
    ("beef", "牛腩", "肉禽", "kg", "45.00"),
    ("egg", "鸡蛋", "蛋奶", "个", "1.20"),
    ("milk", "牛奶", "蛋奶", "L", "12.00"),
    ("fish", "草鱼", "水产", "kg", "12.00"),
    ("shrimp", "虾仁", "水产", "kg", "35.00"),
    ("tofu", "豆腐", "豆制品", "kg", "6.00"),
    ("rice", "大米", "主食", "kg", "6.00"),
    ("noodles", "挂面", "主食", "kg", "8.00"),
    ("flour", "面粉", "主食", "kg", "5.00"),
    ("peanut", "花生米", "坚果", "kg", "12.00"),
    ("seaweed", "紫菜", "水产干货", "kg", "20.00"),
    ("soy_sauce", "酱油", "调料", "L", "10.00"),
    ("salt", "盐", "调料", "kg", "4.00"),
    ("oil", "食用油", "调料", "L", "15.00"),
    ("vinegar", "香醋", "调料", "L", "8.00"),
    ("doubanjiang", "豆瓣酱", "调料", "kg", "12.00"),
    ("starch", "淀粉", "调料", "kg", "6.00"),
    ("dried_chili", "干辣椒", "调料", "kg", "30.00"),
]

# (ingredient_key, 数量, 单位, days 到期 or None) —— 家庭冰箱真实存量
INVENTORY: list[tuple[str, Decimal, str, int | None]] = [
    ("egg", Decimal("6"), "个", 5),
    ("tomato", Decimal("0.5"), "kg", 1),  # 明日到期:临期优先演示
    ("greens", Decimal("0.3"), "kg", 2),
    ("tofu", Decimal("0.4"), "kg", 2),
    ("noodles", Decimal("0.5"), "kg", 30),
    ("pork", Decimal("0.3"), "kg", 3),
    ("potato", Decimal("1"), "kg", 7),
    ("rice", Decimal("2"), "kg", None),
    ("oil", Decimal("0.8"), "L", None),
    ("salt", Decimal("0.3"), "kg", None),
]

# (user_id, 类型, ingredient_key):成员级永久忌口/过敏
RESTRICTIONS: list[tuple[str, str, str]] = [
    (PARTNER_USER_ID, "dislike", "cilantro"),  # 对象忌口香菜
    (PARTNER_USER_ID, "allergy", "shrimp"),  # 对象虾过敏(硬过滤演示)
]

# (user_id, 事件类型, 约束 tags, 起始偏移天, 结束偏移天, 备注)
MEMBER_EVENTS: list[tuple[str, str, list[str], int, int, str]] = [
    (ME_USER_ID, "感冒", ["忌辛辣", "忌油腻", "宜清淡"], 0, 2, "正在感冒,饮食清淡少油"),
    (PARTNER_USER_ID, "例假", ["忌辛辣", "忌生冷"], 0, 1, "例假期,忌辛辣生冷"),
]


def hash_demo_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return f"pbkdf2_sha256$100000${salt}${digest.hex()}"


def _get_or_create_ingredient(
    session: Session, key: str, name: str, category: str, unit: str, price: str
) -> Ingredient:
    ingredient = session.scalar(select(Ingredient).where(Ingredient.key == key))
    if ingredient is None:
        ingredient = Ingredient(key=key, name=name, category=category, default_unit=unit)
        session.add(ingredient)
        session.flush()
    ingredient.reference_price = Decimal(price)
    ingredient.price_source = "市区大型超市公开参考价"
    from app.db.models import utc_now

    ingredient.price_updated_at = utc_now()
    return ingredient


def _get_or_create_user(
    session: Session, user_id: str, email: str, display_name: str
) -> tuple[User, bool]:
    user = session.get(User, user_id)
    created = False
    if user is None:
        created = True
        user = User(
            id=user_id,
            email=email,
            password_hash=hash_demo_password("mealflow-demo"),
            display_name=display_name,
        )
        session.add(user)
        session.flush()
    return user, created


def seed_demo_data() -> dict[str, int]:
    with SessionLocal() as session:  # type: Session
        # 只初始化尚未建立的家庭；已有家庭即使删空库存也不重新灌入演示数据。
        existing = session.get(Workspace, HOME_WORKSPACE_ID)
        if existing is not None:
            return {
                "members": session.scalar(select(func.count()).select_from(WorkspaceMember).where(WorkspaceMember.workspace_id == HOME_WORKSPACE_ID)),
                "events": session.scalar(select(func.count()).select_from(MemberEvent).where(MemberEvent.workspace_id == HOME_WORKSPACE_ID)),
                "recipes": session.scalar(select(func.count()).select_from(Recipe)),
                "ingredients": session.scalar(select(func.count()).select_from(Ingredient)),
                "inventory": session.scalar(select(func.count()).select_from(InventoryItem).where(InventoryItem.workspace_id == HOME_WORKSPACE_ID)),
            }
        tomorrow = date.today() + timedelta(days=1)
        today = date.today()

        # 家庭工作区 / 厨房站点 / 明天午餐餐次(2 人)
        workspace = session.get(Workspace, HOME_WORKSPACE_ID)
        if workspace is None:
            workspace = Workspace(id=HOME_WORKSPACE_ID, name="我家", kind="personal")
            session.add(workspace)
            session.flush()

        site = session.get(Site, HOME_SITE_ID)
        if site is None:
            site = Site(
                id=HOME_SITE_ID, workspace_id=workspace.id, name="家里厨房", service_capacity=4
            )
            session.add(site)
            session.flush()

        me, me_created = _get_or_create_user(session, ME_USER_ID, "me@home.demo", "我")
        if me_created or me.gender is None:
            me.gender = "男"
        if me_created or not me.residency:
            me.residency = "resident"
        partner, partner_created = _get_or_create_user(session, PARTNER_USER_ID, "partner@home.demo", "对象")
        if partner_created or partner.gender is None:
            partner.gender = "女"
        if partner_created or not partner.residency:
            partner.residency = "resident"
        if partner_created or partner.taboo is None:
            partner.taboo = "香菜"
        guest, guest_created = _get_or_create_user(session, GUEST_USER_ID, "guest@home.demo", "老李")
        if guest_created or guest.gender is None:
            guest.gender = "男"
        if guest_created or not guest.residency:
            guest.residency = "guest"
        if guest_created or guest.taboo is None:
            guest.taboo = "海鲜"
        if guest_created or guest.note is None:
            guest.note = "周末偶尔来吃饭"

        for user in (me, partner, guest):
            exists = session.scalar(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == workspace.id,
                    WorkspaceMember.user_id == user.id,
                )
            )
            if exists is None:
                session.add(
                    WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="user")
                )

        meal_service = session.get(MealService, HOME_MEAL_SERVICE_ID)
        if meal_service is None:
            meal_service = MealService(
                id=HOME_MEAL_SERVICE_ID,
                site_id=site.id,
                name="午餐",
                service_date=tomorrow,
                expected_diners=2,
                status="draft",
            )
            session.add(meal_service)
        else:
            meal_service.service_date = tomorrow
            meal_service.expected_diners = 2

        # 成员永久忌口/过敏(user_id 挂到个人)
        for user_id, restriction_type, ingredient_key in RESTRICTIONS:
            exists = session.scalar(
                select(DietaryRestriction).where(
                    DietaryRestriction.workspace_id == workspace.id,
                    DietaryRestriction.user_id == user_id,
                    DietaryRestriction.type == restriction_type,
                    DietaryRestriction.ingredient_key == ingredient_key,
                )
            )
            if exists is None:
                session.add(
                    DietaryRestriction(
                        workspace_id=workspace.id,
                        user_id=user_id,
                        type=restriction_type,
                        ingredient_key=ingredient_key,
                    )
                )

        # 特殊事件(按日期窗口自动生效/过期)
        for user_id, event_type, tags, start_offset, end_offset, note in MEMBER_EVENTS:
            start = today + timedelta(days=start_offset)
            end = today + timedelta(days=end_offset)
            event = session.scalar(
                select(MemberEvent).where(
                    MemberEvent.workspace_id == workspace.id,
                    MemberEvent.user_id == user_id,
                    MemberEvent.event_type == event_type,
                )
            )
            if event is None:
                session.add(
                    MemberEvent(
                        workspace_id=workspace.id,
                        user_id=user_id,
                        event_type=event_type,
                        constraint_tags=tags,
                        start_date=start,
                        end_date=end,
                        note=note,
                    )
                )
            else:
                event.constraint_tags = tags
                event.start_date = start
                event.end_date = end
                event.note = note

        # 食材主数据(带超市参考价)
        ingredients: dict[str, Ingredient] = {}
        for key, name, category, unit, price in INGREDIENTS:
            ingredients[key] = _get_or_create_ingredient(session, key, name, category, unit, price)

        # 冰箱库存(含临期)
        for ingredient_key, quantity, unit, expires_after_days in INVENTORY:
            item = session.scalar(
                select(InventoryItem).where(
                    InventoryItem.workspace_id == workspace.id,
                    InventoryItem.ingredient_id == ingredients[ingredient_key].id,
                )
            )
            expires_on = (
                date.today() + timedelta(days=expires_after_days)
                if expires_after_days is not None
                else None
            )
            if item is None:
                session.add(
                    InventoryItem(
                        workspace_id=workspace.id,
                        ingredient_id=ingredients[ingredient_key].id,
                        quantity=quantity,
                        unit=unit,
                        expires_on=expires_on,
                    )
                )
            # 已有库存由用户维护，种子脚本不再覆盖数量、单位和到期日期。

        # 菜谱与配方明细(data/seed/recipes.json,含 taste_tags)
        payload = json.loads(RECIPE_SEED_PATH.read_text(encoding="utf-8"))
        recipe_count = 0
        for entry in payload["recipes"]:
            recipe = session.scalar(select(Recipe).where(Recipe.name == entry["name"]))
            if recipe is None:
                recipe = Recipe(name=entry["name"])
                session.add(recipe)
            recipe.servings = entry["servings"]
            recipe.duration_minutes = entry["duration_minutes"]
            recipe.kcal_per_serving = entry["kcal_per_serving"]
            recipe.estimated_cost_cents = entry["estimated_cost_cents"]
            recipe.tags = entry["tags"]
            recipe.taste_tags = entry.get("taste_tags", [])
            recipe.allergens = entry["allergens"]
            recipe.steps = entry["steps"]
            recipe.source_name = entry["source_name"]
            recipe.source_url = entry["source_url"] or None
            recipe.license_note = entry["license_note"]
            facts = [
                IngredientFact(
                    key=item["key"],
                    name=ingredients[item["key"]].name,
                    category=ingredients[item["key"]].category,
                    quantity=Decimal(str(item["quantity"])),
                    unit=item["unit"],
                    optional=bool(item.get("optional", False)),
                )
                for item in entry["ingredients"]
            ]
            classified = classify_recipe(entry["name"], entry.get("tags") or [], facts)
            recipe.meal_role = classified.meal_role
            recipe.staple_type = classified.staple_type
            recipe.protein_sources = classified.protein_sources
            recipe.vegetable_keys = classified.vegetable_keys
            recipe.portion_confidence = classified.portion_confidence
            session.flush()

            session.query(RecipeIngredient).filter(
                RecipeIngredient.recipe_id == recipe.id
            ).delete()
            for item in entry["ingredients"]:
                session.add(
                    RecipeIngredient(
                        recipe_id=recipe.id,
                        ingredient_id=ingredients[item["key"]].id,
                        quantity=Decimal(str(item["quantity"])),
                        unit=item["unit"],
                        optional=item.get("optional", False),
                    )
                )
            recipe_count += 1

        session.commit()
        return {
            "members": 3,
            "events": len(MEMBER_EVENTS),
            "recipes": recipe_count,
            "ingredients": len(ingredients),
            "inventory": len(INVENTORY),
        }


def main() -> None:
    counts = seed_demo_data()
    print(
        f"家庭厨房种子完成:{counts['members']} 名成员(含 1 位访客),{counts['events']} 条特殊事件,"
        f"{counts['recipes']} 道菜谱,{counts['ingredients']} 种食材(含超市参考价),"
        f"冰箱 {counts['inventory']} 项库存。"
    )
    print("演示场景:我感冒中(忌辛辣油腻)/ 对象例假期+忌香菜+虾过敏 / 明天午餐 2 人 / 辣子鸡冲突警示。")


if __name__ == "__main__":
    main()
