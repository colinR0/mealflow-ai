from pathlib import Path

from sqlalchemy import select

from app.cli.register_recipe_sources import Candidate, register
from app.db.session import SessionLocal
from app.db.source_registry_models import RecipeSourceAsset, RecipeSourceAssetVersion


def test_registration_skips_unchanged_bytes_and_versions_changes() -> None:
    candidate = Candidate(
        path=Path("data/local/source-registration-test.txt"),
        source_key="data/local/source-registration-test.txt",
        format="txt",
    )
    first_digest = "a" * 64
    second_digest = "b" * 64

    assert register(candidate, first_digest, 12) == ("registered", 1)
    assert register(candidate, first_digest, 12) == ("skipped", 1)
    assert register(candidate, second_digest, 13) == ("updated", 2)

    with SessionLocal() as session:
        asset = session.execute(
            select(RecipeSourceAsset).where(RecipeSourceAsset.source_key == candidate.source_key)
        ).scalar_one()
        versions = session.execute(
            select(RecipeSourceAssetVersion)
            .where(RecipeSourceAssetVersion.asset_id == asset.id)
            .order_by(RecipeSourceAssetVersion.version)
        ).scalars().all()

    assert asset.current_version == 2
    assert [(item.version, item.sha256, item.status) for item in versions] == [
        (1, first_digest, "discovered"),
        (2, second_digest, "discovered"),
    ]
