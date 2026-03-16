from __future__ import annotations

from pydantic import BaseModel


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


class ErrorResponse(BaseModel):
    error: str
    message: str
