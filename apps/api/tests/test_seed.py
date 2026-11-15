from sqlalchemy import select

from app.db.models import Ingredient
from app.db.seed import INGREDIENTS, seed_demo_data
from app.db.session import SessionLocal


def test_seed_is_idempotent_and_keeps_ingredient_prices() -> None:
    seed_demo_data()
    seed_demo_data()

    with SessionLocal() as session:
        rows = session.scalars(select(Ingredient)).all()
        by_key = {item.key: item for item in rows}
        assert len(by_key) >= len(INGREDIENTS)
        egg = by_key["egg"]
        assert egg.reference_price is not None and egg.reference_price > 0
        assert egg.name == "鸡蛋"
