from __future__ import annotations

import asyncio
import logging

import httpx

from server.config import CATALOG_REFRESH_SECONDS, CATALOG_URL

logger = logging.getLogger(__name__)

_product_ids: set[str] = set()
_loaded: bool = False
_refresh_task: asyncio.Task | None = None


async def load_catalog() -> None:
    global _product_ids, _loaded
    if not CATALOG_URL:
        logger.info("CATALOG_URL not set, skipping catalog load")
        _loaded = True
        return
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(CATALOG_URL, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        ids: set[str] = set()
        for cat in data.get("categories", []):
            for prod in cat.get("products", []):
                pid = prod.get("id")
                if pid:
                    ids.add(pid)
        _product_ids = ids
        _loaded = True
        logger.info("Catalog loaded: %d products", len(ids))
    except Exception:
        logger.exception("Failed to load catalog")
        if not _loaded:
            logger.warning("No cached catalog available")


async def _refresh_loop() -> None:
    while True:
        await asyncio.sleep(CATALOG_REFRESH_SECONDS)
        await load_catalog()


def start_refresh() -> None:
    global _refresh_task
    _refresh_task = asyncio.create_task(_refresh_loop())


def stop_refresh() -> None:
    global _refresh_task
    if _refresh_task:
        _refresh_task.cancel()
        _refresh_task = None


def is_loaded() -> bool:
    return _loaded


def is_valid_product(product_id: str) -> bool:
    return product_id in _product_ids


def product_count() -> int:
    return len(_product_ids)
