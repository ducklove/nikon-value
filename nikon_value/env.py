"""시크릿 파일·환경변수 로딩 유틸."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from nikon_value.paths import PROJECT_ROOT

log = logging.getLogger(__name__)


def load_text_secret(filename: str, env_name: str) -> str | None:
    """KEY=VALUE 또는 raw text 형식의 시크릿 파일을 읽습니다."""
    key_file = PROJECT_ROOT / filename
    if key_file.exists():
        with open(key_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    _, value = line.split("=", 1)
                    return value.strip()
                return line
    return os.environ.get(env_name)


def load_env_file(path: Path):
    """KEY=VALUE 형식의 환경변수 파일을 로드합니다."""
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())
    log.info("Loaded credentials from %s", path)
