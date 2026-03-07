#!/usr/bin/env python3
"""Publish the generated static site into the repository root.

This keeps branch-based GitHub Pages deployments working even when the
primary build output is generated into dist/.
"""

from __future__ import annotations

import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = PROJECT_ROOT / "dist"
PRODUCTS_DIR = PROJECT_ROOT / "products"
FILES_TO_COPY = [
    "index.html",
    "404.html",
    "robots.txt",
    "sitemap.xml",
    ".nojekyll",
]


def copy_file(name: str) -> None:
    source = DIST_DIR / name
    if source.exists():
        shutil.copy2(source, PROJECT_ROOT / name)


def main() -> None:
    if not DIST_DIR.exists():
        raise SystemExit("dist/ does not exist. Run build_static_site.py first.")

    for filename in FILES_TO_COPY:
        copy_file(filename)

    if PRODUCTS_DIR.exists():
        shutil.rmtree(PRODUCTS_DIR)
    shutil.copytree(DIST_DIR / "products", PRODUCTS_DIR)


if __name__ == "__main__":
    main()
