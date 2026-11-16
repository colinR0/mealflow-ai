from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# 仓库根目录（apps/api/app/core/config.py -> 上五级，与 seed.py 的 parents[4] 一致）。
# 数据库等相对路径统一锚定到这里，避免从 apps/api 或 IDE 启动时
# 解析成 apps/api/data/local 下的另一份副本，导致两人看到不同数据。
REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_name: str = "智慧冰箱服务"
    api_v1_prefix: str = "/api/v1"
    database_url: str = (
        "postgresql+psycopg://mealflow@127.0.0.1:5434/mealflow_s7?gssencmode=disable"
    )
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]
    demo_mode: bool = True
    # 做饭记录照片先写入本地目录；生产对象存储通过该适配点替换。
    cooking_photo_dir: str = "data/cooking_photos"
    # 本地 SAM 3 只认静态照片。空地址表示未配置，接口会明确拒绝而不是伪造结果。
    fridge_vision_url: str = "http://127.0.0.1:8091"
    fridge_vision_timeout_seconds: float = Field(default=180, gt=0, le=300)

    llm_provider: str = "qwen"
    llm_api_style: str = "chat_completions"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: int = 30
    aitechflux_api_key: str = ""
    aitechflux_base_url: str = "https://aitechflux.com/v1"
    aitechflux_gpt_model: str = "gpt-6.1-sol"
    aitechflux_grok_model: str = "grok-4.7"
    # 智谱模型：文本 Agent 与视觉理解分开配置，共用同一平台密钥。
    bigmodel_api_key: str = ""
    bigmodel_base_url: str = "https://open.bigmodel.cn/api/paas/v4/"
    bigmodel_text_model: str = "glm-4.5-air"
    bigmodel_vision_model: str = "glm-4.6v"
    # 仅用于对话 Agent 的只读工具循环；正式配餐图不受该值影响。
    agent_max_steps: int = 6
    runtime_household_active_limit: int = Field(default=2, ge=1)
    runtime_queue_capacity: int = Field(default=100, ge=1)
    retrieval_mode: Literal["demo", "native"] = "demo"
    retrieval_embedding_model: str = "fake"
    retrieval_embedding_dimensions: int = Field(default=64, ge=1)
    retrieval_template_version: str = "v1"
    retrieval_tokenizer_version: str = "v1"
    retrieval_embedding_device: str = "cpu"
    retrieval_embedding_batch_size: int = Field(default=16, ge=1, le=128)
    # BGE 中文检索模型卡推荐的查询前缀；文档向量不加前缀。
    retrieval_embedding_query_prefix: str = "为这个句子生成表示以用于检索相关文章："
    retrieval_embedding_local_files_only: bool = False
    retrieval_embedding_max_seq_length: int | None = Field(default=None, ge=1)
    # 查询期 cosine 相似度下限，只影响召回，不参与 embedding profile 身份。
    # 0.40 来自 bge-small-zh-v1.5 实测：相关 top1 >= 0.4307，无关 top1 <= 0.3834。
    retrieval_min_similarity: float = Field(default=0.41, ge=0.0, le=1.0)
    # 关键字通道的下限相对系数。精确词命中与语义相似度不是同一个尺度：真的烹饪
    # 问题可能命中正文度 0.34-0.40 的相关文档，而完全无关的正文约在 0.22。
    # 因此关键字命中只需越过 0.41 * 0.5 ≈ 0.21 这个"明显无关"区。
    keyword_similarity_ratio: float = Field(default=0.5, ge=0.0, le=1.0)
    # DuckDuckGo HTML is not a stable production provider. Enable only after
    # configuring a reliable search endpoint; local knowledge remains available.
    knowledge_web_search_enabled: bool = False
    knowledge_web_search_timeout_seconds: float = Field(default=6, gt=0, le=15)
    # Named-dish fallback scrapes a recipe site when the local library misses.
    # It is independent from the knowledge web-search switch above, because a
    # named dish must still get a tutorial when cooking-skill search is offline.
    recipe_web_fallback_enabled: bool = True
    kiosk_token_secret: str = "replace-in-real-environment"

    @field_validator("database_url", mode="after")
    @classmethod
    def require_postgresql(cls, value: str) -> str:
        """Require PostgreSQL for every application database connection."""
        if not value.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("DATABASE_URL must use PostgreSQL")
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("agent_max_steps", mode="after")
    @classmethod
    def clamp_agent_max_steps(cls, value: int) -> int:
        """工具循环保持有限，防止意外配置导致无限查询或不可控成本。"""
        return max(1, min(value, 8))


@lru_cache
def get_settings() -> Settings:
    return Settings()
