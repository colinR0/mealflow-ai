from fastapi import APIRouter

from app.api.routes import health, household, inventory, shared_data

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(inventory.router)
api_router.include_router(household.router)
api_router.include_router(shared_data.router)
