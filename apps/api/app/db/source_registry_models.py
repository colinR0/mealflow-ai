"""File-level ingestion inventory; recipe documents are created only after validation."""

from sqlalchemy import BigInteger, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models import TimestampMixin, new_id


class RecipeSourceAsset(TimestampMixin, Base):
    __tablename__ = "recipe_source_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    # Repository-relative paths remain stable when the workspace moves to another machine.
    source_key: Mapped[str] = mapped_column(String(1024), unique=True)
    format: Mapped[str] = mapped_column(String(16))
    current_version: Mapped[int] = mapped_column(Integer, default=0)


class RecipeSourceAssetVersion(TimestampMixin, Base):
    __tablename__ = "recipe_source_asset_versions"
    __table_args__ = (UniqueConstraint("asset_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    asset_id: Mapped[str] = mapped_column(ForeignKey("recipe_source_assets.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(24), default="discovered", index=True)
    parser_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    parsed_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
