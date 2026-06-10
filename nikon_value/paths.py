"""저장소 공통 경로 상수."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "products.yaml"
DATA_DIR = PROJECT_ROOT / "data"
CATALOG_JSON_PATH = DATA_DIR / "catalog.json"
