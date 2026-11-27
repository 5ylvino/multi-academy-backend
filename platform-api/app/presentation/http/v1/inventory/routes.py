"""Inventory: stock items, movements and the store summary."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status
from pydantic import Field

from app.presentation.http.dependencies.auth import SchoolPrincipal
from app.presentation.http.dependencies.context import UseCasesDep
from app.presentation.http.v1.schemas import ApiModel, PageMeta
from app.use_cases.inventory.stock import ItemDetails, MoveCommand

router = APIRouter(prefix="/inventory", tags=["inventory"])

UNIT_HELP = "piece, box, pack, carton, ream, set, pair, bottle, litre, kg or metre"


class ItemRequest(ApiModel):
    name: str = Field(min_length=2, max_length=160)
    unit: str = Field(default="piece", max_length=16, description=UNIT_HELP)
    reorder_level: int = Field(default=0, ge=0, le=10_000_000)
    sku: str | None = Field(default=None, max_length=40)
    category: str | None = Field(default=None, max_length=60)
    location: str | None = Field(default=None, max_length=120)


class UpdateItemRequest(ItemRequest):
    active: bool | None = None


class ItemResponse(ApiModel):
    id: UUID
    name: str
    sku: str | None
    category: str | None
    unit: str
    on_hand: int
    reorder_level: int
    low: bool = Field(description="At or below the reorder level.")
    location: str | None
    currency: str
    unit_cost_minor: int | None = Field(description="Last price paid per unit.")
    value_minor: int | None
    active: bool
    updated_at: datetime


class ItemPage(ApiModel):
    items: list[ItemResponse]
    page: PageMeta


class MoveRequest(ApiModel):
    kind: str = Field(pattern="^(receive|issue|return|adjust|write_off)$")
    quantity: int = Field(
        ge=0,
        le=10_000_000,
        description="How many moved; for adjust, the quantity counted on the shelf.",
    )
    reason: str | None = Field(
        default=None, max_length=300, description="Required for adjust and write_off."
    )
    issued_to: UUID | None = Field(default=None, description="Who received it (issue, return).")
    reference: str | None = Field(
        default=None, max_length=120, description="e.g. an expense number"
    )
    unit_cost_minor: int | None = Field(default=None, ge=0, le=10**12, description="For receive.")


class MovementResponse(ApiModel):
    id: UUID
    item_id: UUID
    kind: str
    quantity: int = Field(description="Signed change to stock.")
    balance_after: int
    unit_cost_minor: int | None
    reason: str | None
    issued_to: UUID | None
    reference: str | None
    by: UUID
    at: datetime


class MoveResponse(ApiModel):
    item: ItemResponse
    movement: MovementResponse


class MovementPage(ApiModel):
    items: list[MovementResponse]
    page: PageMeta


class SummaryResponse(ApiModel):
    items: int
    low_stock: int
    value_by_currency: dict[str, int]


def _details(body: ItemRequest) -> ItemDetails:
    return ItemDetails(
        name=body.name,
        unit=body.unit,
        reorder_level=body.reorder_level,
        sku=body.sku,
        category=body.category,
        location=body.location,
    )


@router.get("/summary", response_model=SummaryResponse)
async def summary(principal: SchoolPrincipal, uc: UseCasesDep) -> SummaryResponse:
    return SummaryResponse.model_validate(await uc.inventory.summary(principal))


@router.post("/items", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
async def add_item(body: ItemRequest, principal: SchoolPrincipal, uc: UseCasesDep) -> ItemResponse:
    """New items start at zero; receive stock to fill them."""
    return ItemResponse.model_validate(await uc.inventory.add_item(principal, _details(body)))


@router.get("/items", response_model=ItemPage)
async def list_items(
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    search: Annotated[str | None, Query(max_length=80)] = None,
    category: Annotated[str | None, Query(max_length=60)] = None,
    low_only: bool = False,
    include_inactive: bool = False,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ItemPage:
    page = await uc.inventory.list(
        principal,
        search=search,
        category=category,
        low_only=low_only,
        include_inactive=include_inactive,
        cursor=cursor,
        limit=limit,
    )
    return ItemPage(
        items=[ItemResponse.model_validate(v) for v in page.items],
        page=PageMeta(next_cursor=page.next_cursor),
    )


@router.get("/items/{item_id}", response_model=ItemResponse)
async def get_item(item_id: UUID, principal: SchoolPrincipal, uc: UseCasesDep) -> ItemResponse:
    return ItemResponse.model_validate(await uc.inventory.get(principal, item_id))


@router.put("/items/{item_id}", response_model=ItemResponse)
async def update_item(
    item_id: UUID, body: UpdateItemRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> ItemResponse:
    """Change the details or retire (``active: false``). Quantities change only by movements."""
    view = await uc.inventory.update_item(principal, item_id, _details(body), active=body.active)
    return ItemResponse.model_validate(view)


@router.post(
    "/items/{item_id}/movements", response_model=MoveResponse, status_code=status.HTTP_201_CREATED
)
async def move_stock(
    item_id: UUID, body: MoveRequest, principal: SchoolPrincipal, uc: UseCasesDep
) -> MoveResponse:
    """Receive, issue, return, write off, or correct after a stock count."""
    result = await uc.inventory.move(
        principal,
        item_id,
        MoveCommand(
            kind=body.kind,
            quantity=body.quantity,
            reason=body.reason,
            issued_to=body.issued_to,
            reference=body.reference,
            unit_cost_minor=body.unit_cost_minor,
        ),
    )
    return MoveResponse.model_validate(result)


@router.get("/movements", response_model=MovementPage)
async def list_movements(
    principal: SchoolPrincipal,
    uc: UseCasesDep,
    item_id: UUID | None = None,
    issued_to: UUID | None = None,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> MovementPage:
    """Newest first. Filter by item, or by person to see what someone holds."""
    page = await uc.inventory.movements(
        principal, item_id=item_id, issued_to=issued_to, cursor=cursor, limit=limit
    )
    return MovementPage(
        items=[MovementResponse.model_validate(v) for v in page.items],
        page=PageMeta(next_cursor=page.next_cursor),
    )
