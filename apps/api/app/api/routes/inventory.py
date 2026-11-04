from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.schemas.inventory import (
    IngredientView,
    InventoryCreate,
    InventoryItemView,
    InventoryUpdate,
)
from app.services import inventory_service

router = APIRouter(tags=["inventory"])


def _db() -> Session:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@router.get("/ingredients", response_model=list[IngredientView])
def list_ingredients(session: Session = Depends(_db)) -> list[IngredientView]:
    return [IngredientView(**row) for row in inventory_service.list_ingredients(session)]


@router.get(
    "/workspaces/{workspace_id}/inventory", response_model=list[InventoryItemView]
)
def list_inventory(workspace_id: str, session: Session = Depends(_db)) -> list[InventoryItemView]:
    try:
        return inventory_service.list_items(session, workspace_id)
    except inventory_service.WorkspaceNotFoundError as exc:  # Map missing workspace to 404.
        raise HTTPException(status_code=404, detail=str(exc))


@router.post(
    "/workspaces/{workspace_id}/inventory",
    response_model=InventoryItemView,
    status_code=status.HTTP_201_CREATED,
)
def add_inventory_item(
    workspace_id: str, payload: InventoryCreate, session: Session = Depends(_db)
) -> InventoryItemView:
    try:
        return inventory_service.add_item(session, workspace_id, payload)
    except inventory_service.WorkspaceNotFoundError as exc:  # Map missing workspace to 404.
        raise HTTPException(status_code=404, detail=str(exc))
    except inventory_service.IngredientNotFoundError as exc:  # Map unknown ingredient to 400.
        raise HTTPException(status_code=400, detail=str(exc))
    except inventory_service.UnitMismatchError as exc:  # Map incompatible units to 400.
        raise HTTPException(status_code=400, detail=str(exc))


@router.patch("/inventory/{item_id}", response_model=InventoryItemView)
def update_inventory_item(
    item_id: str, payload: InventoryUpdate, session: Session = Depends(_db)
) -> InventoryItemView:
    try:
        return inventory_service.update_item(session, item_id, payload)
    except inventory_service.ItemNotFoundError as exc:  # Map missing inventory item to 404.
        raise HTTPException(status_code=404, detail=str(exc))
    except inventory_service.UnitMismatchError as exc:  # Map incompatible units to 400.
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/inventory/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_inventory_item(item_id: str, session: Session = Depends(_db)) -> None:
    try:
        inventory_service.delete_item(session, item_id)
    except inventory_service.ItemNotFoundError as exc:  # Map missing inventory item to 404.
        raise HTTPException(status_code=404, detail=str(exc))
