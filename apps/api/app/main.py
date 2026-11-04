from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.api.routes.v2 import router as v2_router
from app.core.config import get_settings
from app.db.schema_guard import DatabaseSchemaOutdatedError, ensure_schema_current


settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """拒绝在代码结构领先数据库时启动，防止请求进入后才集体工具失败。"""
    try:
        ensure_schema_current()
    except DatabaseSchemaOutdatedError:  # Log the schema mismatch before refusing startup.
        logger.exception("数据库迁移未完成，API 已拒绝启动")
        raise
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    # v2 uses server-side Cookie sessions; writes also require Origin and CSRF checks.
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key", "Last-Event-ID"],
)
app.include_router(api_router, prefix=settings.api_v1_prefix)
app.include_router(v2_router)


@app.get("/", tags=["meta"])
def root() -> dict[str, str]:
    return {"name": settings.app_name, "docs": "/docs", "health": "/api/v1/health"}
