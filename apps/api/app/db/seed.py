"""Seed the ingredient dictionary used by inventory and recipe classification.

Retired demo households, old inventory rows, and business recipes are no longer
created here. Run from apps/api: python -m app.db.seed
"""

import hashlib
import secrets
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Ingredient, User, Workspace, WorkspaceMember, utc_now
from app.db.session import SessionLocal
from app.services.ingredient_catalog import ensure_preset_aliases

HOME_WORKSPACE_ID = "home-workspace"
DEMO_USERS = (
    ("home-me", "me@home.demo", "我", "男", "resident"),
    ("home-partner", "partner@home.demo", "对象", "女", "resident"),
    ("home-guest", "guest@home.demo", "老李", "男", "guest"),
)

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


def hash_demo_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return f"pbkdf2_sha256$100000${salt}${digest.hex()}"


def _upsert_ingredient(
    session: Session, key: str, name: str, category: str, unit: str, price: str
) -> Ingredient:
    ingredient = session.scalar(select(Ingredient).where(Ingredient.key == key))
    if ingredient is None:
        ingredient = Ingredient(key=key, name=name, category=category, default_unit=unit)
        session.add(ingredient)
        session.flush()
    ingredient.name = name
    ingredient.category = category
    ingredient.default_unit = unit
    ingredient.reference_price = Decimal(price)
    ingredient.price_source = "市区大型超市公开参考价"
    ingredient.price_updated_at = utc_now()
    return ingredient


def _ensure_demo_household(session: Session) -> None:
    """Tests need one household membership. This does not restore retired tables."""
    workspace = session.get(Workspace, HOME_WORKSPACE_ID)
    if workspace is None:
        workspace = Workspace(id=HOME_WORKSPACE_ID, name="我家", kind="personal")
        session.add(workspace)
        session.flush()
    for user_id, email, display_name, gender, residency in DEMO_USERS:
        user = session.get(User, user_id)
        if user is None:
            user = User(
                id=user_id,
                email=email,
                password_hash=hash_demo_password("mealflow-demo"),
                display_name=display_name,
                gender=gender,
                residency=residency,
            )
            session.add(user)
            session.flush()
        membership = session.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace.id,
                WorkspaceMember.user_id == user.id,
            )
        )
        if membership is None:
            session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="user"))


def seed_demo_data() -> dict[str, int]:
    with SessionLocal() as session:
        _ensure_demo_household(session)
        for key, name, category, unit, price in INGREDIENTS:
            _upsert_ingredient(session, key, name, category, unit, price)
        ensure_preset_aliases(session)
        session.commit()
        count = session.scalar(select(func.count()).select_from(Ingredient))
    return {"ingredients": int(count or 0)}


def main() -> None:
    counts = seed_demo_data()
    print(f"食材字典种子完成: {counts['ingredients']} 种食材。")


if __name__ == "__main__":
    main()
