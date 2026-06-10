from __future__ import annotations

import asyncio
import logging

import httpx

from server.config import CATALOG_REFRESH_SECONDS, CATALOG_URL

logger = logging.getLogger(__name__)

_product_ids: set[str] = set()
_product_medians: dict[str, float | None] = {}
_product_names: dict[str, str] = {}
_loaded: bool = False
_refresh_task: asyncio.Task | None = None


async def load_catalog() -> None:
    global _product_ids, _product_medians, _product_names, _loaded
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
        medians: dict[str, float | None] = {}
        names: dict[str, str] = {}
        for cat in data.get("categories", []):
            for prod in cat.get("products", []):
                pid = prod.get("id")
                if pid:
                    ids.add(pid)
                    medians[pid] = prod.get("median")
                    names[pid] = prod.get("name_ko") or prod.get("name_en") or pid
        _product_ids = ids
        _product_medians = medians
        _product_names = names
        _loaded = True
        logger.info("Catalog loaded: %d products", len(ids))
    except Exception:
        logger.exception("Failed to load catalog")
        if not _loaded:
            logger.warning("No cached catalog available")


async def _refresh_loop(on_refresh=None) -> None:
    while True:
        await asyncio.sleep(CATALOG_REFRESH_SECONDS)
        try:
            await load_catalog()
            if on_refresh is not None and _loaded:
                await on_refresh()
        except Exception:
            # load_catalog가 네트워크 오류는 자체 처리하지만, 루프 자체는 어떤
            # 예외에도 죽지 않아야 다음 주기 갱신이 보장된다.
            logger.exception("Catalog refresh iteration failed")


def start_refresh(on_refresh=None) -> None:
    global _refresh_task
    _refresh_task = asyncio.create_task(_refresh_loop(on_refresh))


def stop_refresh() -> None:
    global _refresh_task
    if _refresh_task:
        _refresh_task.cancel()
        _refresh_task = None


def is_loaded() -> bool:
    return _loaded


def is_valid_product(product_id: str) -> bool:
    return product_id in _product_ids


def get_median(product_id: str) -> float | None:
    return _product_medians.get(product_id)


def get_product_name(product_id: str) -> str:
    return _product_names.get(product_id, product_id)


def product_count() -> int:
    return len(_product_ids)
