from __future__ import annotations

from pydantic import BaseModel, Field


class UserResponse(BaseModel):
    id: int
    provider: str
    name: str | None
    email: str | None


class FavoritesResponse(BaseModel):
    favorites: list[str]


class HealthResponse(BaseModel):
    status: str
    db: str
    catalog_loaded: bool
    catalog_products: int
    uptime_seconds: int


class AlertRequest(BaseModel):
    target_price: float = Field(gt=0, le=1_000_000)


class AlertEntry(BaseModel):
    product_id: str
    target_price: float
    triggered: bool


class AlertsResponse(BaseModel):
    alerts: list[AlertEntry]


class ErrorResponse(BaseModel):
    error: str
    message: str
