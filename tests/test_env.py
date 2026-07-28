"""시크릿 파일·환경변수 로딩(nikon_value.env) 테스트.

읽는 디렉터리를 주입할 수 있으므로 저장소 루트의 실제 키 파일과 무관하게
tmp_path만으로 전 분기를 검증한다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nikon_value.env import load_env_file, load_text_secret


@pytest.fixture
def isolated_environ(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """os.environ을 사본으로 교체해 테스트가 프로세스 환경을 오염시키지 않게 한다."""
    fake = dict(os.environ)
    monkeypatch.setattr(os, 'environ', fake)
    return fake


# --------------------------------------------------------------------------- #
# load_text_secret
# --------------------------------------------------------------------------- #


def test_load_text_secret_reads_the_key_value_form(tmp_path: Path) -> None:
    (tmp_path / 'openrouter.key').write_text('OPENROUTER_API_KEY=sk-secret\n', encoding='utf-8')

    assert load_text_secret('openrouter.key', 'OPENROUTER_API_KEY', base_dir=tmp_path) == 'sk-secret'


def test_load_text_secret_reads_the_raw_text_form(tmp_path: Path) -> None:
    (tmp_path / 'openrouter.key').write_text('  sk-secret  \n', encoding='utf-8')

    assert load_text_secret('openrouter.key', 'OPENROUTER_API_KEY', base_dir=tmp_path) == 'sk-secret'


def test_load_text_secret_skips_comments_and_blank_lines(tmp_path: Path) -> None:
    (tmp_path / 'openrouter.key').write_text(
        '# OpenRouter API key\n\n   \nOPENROUTER_API_KEY = sk-secret \n',
        encoding='utf-8',
    )

    assert load_text_secret('openrouter.key', 'OPENROUTER_API_KEY', base_dir=tmp_path) == 'sk-secret'


def test_load_text_secret_takes_only_the_first_meaningful_line(tmp_path: Path) -> None:
    (tmp_path / 'openrouter.key').write_text('sk-first\nsk-second\n', encoding='utf-8')

    assert load_text_secret('openrouter.key', 'OPENROUTER_API_KEY', base_dir=tmp_path) == 'sk-first'


def test_load_text_secret_falls_back_to_the_environment_variable(
    tmp_path: Path, isolated_environ: dict[str, str]
) -> None:
    isolated_environ['OPENROUTER_API_KEY'] = 'sk-from-env'

    assert load_text_secret('nope.key', 'OPENROUTER_API_KEY', base_dir=tmp_path) == 'sk-from-env'


def test_load_text_secret_returns_none_without_a_file_or_env(
    tmp_path: Path, isolated_environ: dict[str, str]
) -> None:
    isolated_environ.pop('OPENROUTER_API_KEY', None)

    assert load_text_secret('nope.key', 'OPENROUTER_API_KEY', base_dir=tmp_path) is None


def test_load_text_secret_falls_back_when_the_file_has_only_comments(
    tmp_path: Path, isolated_environ: dict[str, str]
) -> None:
    """주석뿐인 파일은 읽을 값이 없으므로 환경변수 폴백과 같은 결과여야 한다."""
    (tmp_path / 'openrouter.key').write_text('# placeholder\n\n', encoding='utf-8')
    isolated_environ['OPENROUTER_API_KEY'] = 'sk-from-env'

    assert load_text_secret('openrouter.key', 'OPENROUTER_API_KEY', base_dir=tmp_path) == 'sk-from-env'


# --------------------------------------------------------------------------- #
# load_env_file
# --------------------------------------------------------------------------- #


def test_load_env_file_populates_the_environment(
    tmp_path: Path, isolated_environ: dict[str, str]
) -> None:
    path = tmp_path / 'ebay.key'
    path.write_text('EBAY_APP_ID = app-id \nEBAY_CERT_ID=cert-id\n', encoding='utf-8')
    isolated_environ.pop('EBAY_APP_ID', None)
    isolated_environ.pop('EBAY_CERT_ID', None)

    load_env_file(path)

    assert isolated_environ['EBAY_APP_ID'] == 'app-id'
    assert isolated_environ['EBAY_CERT_ID'] == 'cert-id'


def test_load_env_file_never_overwrites_an_existing_variable(
    tmp_path: Path, isolated_environ: dict[str, str]
) -> None:
    """setdefault 의미 — CI 시크릿이 로컬 키 파일보다 우선해야 한다."""
    path = tmp_path / 'ebay.key'
    path.write_text('EBAY_APP_ID=from-file\n', encoding='utf-8')
    isolated_environ['EBAY_APP_ID'] = 'from-ci'

    load_env_file(path)

    assert isolated_environ['EBAY_APP_ID'] == 'from-ci'


def test_load_env_file_ignores_comments_blanks_and_valueless_lines(
    tmp_path: Path, isolated_environ: dict[str, str]
) -> None:
    path = tmp_path / 'ebay.key'
    path.write_text(
        '# eBay credentials\n\nJUST_A_TOKEN\nEBAY_APP_ID=app-id\n',
        encoding='utf-8',
    )
    isolated_environ.pop('EBAY_APP_ID', None)
    isolated_environ.pop('JUST_A_TOKEN', None)

    load_env_file(path)

    assert isolated_environ['EBAY_APP_ID'] == 'app-id'
    assert 'JUST_A_TOKEN' not in isolated_environ


def test_load_env_file_is_a_noop_for_a_missing_file(
    tmp_path: Path, isolated_environ: dict[str, str]
) -> None:
    before = dict(isolated_environ)

    load_env_file(tmp_path / 'nope.key')

    assert isolated_environ == before
