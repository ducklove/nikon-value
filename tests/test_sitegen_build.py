"""정적 사이트 빌드 CLI(nikon_value.sitegen.build)의 단위 테스트.

경로를 주입할 수 있게 열려 있으므로 저장소 실데이터 없이 가짜 소스 루트와
tmp_path 산출물 디렉터리만으로 전 경로를 태운다. 산출물 HTML의 내용 검증은
tests/test_build_output.py(실데이터 풀빌드)와 tests/test_sitegen_golden.py가
담당하므로 여기서는 파일 배치·복사·CLI 분기만 다룬다.
"""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

import pytest

from nikon_value.paths import PROJECT_ROOT
from nikon_value.sitegen import build

BASE_URL = 'https://example.test/nikon-value'

# ASSET_COPY_PLAN으로 옮기기 전에 개별 shutil.copy2로 나열돼 있던 에셋 상수들.
# 공개 API로 재노출 중이라 유지하되, 목록과 어긋나지 않는지 아래에서 검증한다.
LEGACY_ASSET_CONSTANTS = (
    build.STYLE_PATH,
    build.SITE_JS_PATH,
    build.AUTH_JS_PATH,
    build.HERO_JPG,
    build.HERO_WEBP_800,
    build.HERO_WEBP_1600,
    build.FILM_HISTORY_JPG_1,
    build.FILM_HISTORY_JPG_2,
    build.LENS_HERO_JPG,
    build.EBAY_LOGO,
)


def _make_source_root(tmp_path: Path) -> Path:
    """ASSET_COPY_PLAN이 요구하는 소스 파일을 모두 갖춘 가짜 저장소 루트."""
    root = tmp_path / 'repo'
    for source_rel, _ in build.ASSET_COPY_PLAN:
        source = root / source_rel
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(f'stub:{source_rel}', encoding='utf-8')
    return root


def _make_data_dir(tmp_path: Path) -> Path:
    """catalog.json과 products/ 히스토리를 갖춘 가짜 데이터 디렉터리."""
    data_dir = tmp_path / 'data'
    (data_dir / 'products').mkdir(parents=True)
    (data_dir / 'catalog.json').write_text('{"categories": []}', encoding='utf-8')
    (data_dir / 'products' / 'nikon-fm2.json').write_text('[]', encoding='utf-8')
    return data_dir


# --------------------------------------------------------------------------- #
# parse_args
# --------------------------------------------------------------------------- #


def test_parse_args_defaults_to_the_repository_dist_directory() -> None:
    args = build.parse_args([])

    assert args.output == str(build.DEFAULT_OUTPUT)
    assert args.base_url == ''
    assert args.publish_root is False


def test_parse_args_reads_every_flag() -> None:
    args = build.parse_args(['--output', '/tmp/site', '--base-url', BASE_URL, '--publish-root'])

    assert args.output == '/tmp/site'
    assert args.base_url == BASE_URL
    assert args.publish_root is True


# --------------------------------------------------------------------------- #
# detect_base_url
# --------------------------------------------------------------------------- #


@pytest.fixture
def no_base_url_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """base URL 자동 감지가 로컬 환경변수에 좌우되지 않게 지운다."""
    monkeypatch.delenv('SITE_BASE_URL', raising=False)
    monkeypatch.delenv('GITHUB_REPOSITORY', raising=False)


def _fake_git_remote(monkeypatch: pytest.MonkeyPatch, remote: str | Exception) -> None:
    """build 모듈이 보는 subprocess만 교체해 git remote 조회 결과를 고정한다."""

    def check_output(*_args: Any, **_kwargs: Any) -> str:
        if isinstance(remote, Exception):
            raise remote
        return f'{remote}\n'

    monkeypatch.setattr(build, 'subprocess', types.SimpleNamespace(check_output=check_output))


@pytest.mark.usefixtures('no_base_url_env')
def test_detect_base_url_prefers_the_cli_value_and_strips_the_trailing_slash() -> None:
    assert build.detect_base_url(f'{BASE_URL}/') == BASE_URL


@pytest.mark.usefixtures('no_base_url_env')
def test_detect_base_url_falls_back_to_site_base_url_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('SITE_BASE_URL', f'{BASE_URL}/')

    assert build.detect_base_url('') == BASE_URL


@pytest.mark.usefixtures('no_base_url_env')
def test_detect_base_url_derives_pages_url_from_github_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('GITHUB_REPOSITORY', 'ducklove/nikon-value')

    assert build.detect_base_url('') == 'https://ducklove.github.io/nikon-value'


@pytest.mark.usefixtures('no_base_url_env')
def test_detect_base_url_ignores_a_github_repository_without_a_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('GITHUB_REPOSITORY', 'nikon-value')
    _fake_git_remote(monkeypatch, 'git@github.com:ducklove/nikon-value.git')

    # 슬래시가 없으면 owner/repo를 못 나누므로 git remote 경로로 넘어간다.
    assert build.detect_base_url('') == 'https://ducklove.github.io/nikon-value'


@pytest.mark.usefixtures('no_base_url_env')
@pytest.mark.parametrize(
    'remote',
    [
        'git@github.com:ducklove/nikon-value.git',
        'git@github.com:ducklove/nikon-value',
        'https://github.com/ducklove/nikon-value.git',
        'https://github.com/ducklove/nikon-value',
    ],
)
def test_detect_base_url_parses_ssh_and_https_git_remotes(
    monkeypatch: pytest.MonkeyPatch, remote: str
) -> None:
    _fake_git_remote(monkeypatch, remote)

    assert build.detect_base_url('') == 'https://ducklove.github.io/nikon-value'


@pytest.mark.usefixtures('no_base_url_env')
@pytest.mark.parametrize(
    'remote',
    [
        'https://gitlab.com/ducklove/nikon-value.git',  # GitHub이 아닌 호스트
        'git@github.com:nikon-value',  # owner/repo로 나눌 수 없음
        'https://github.com/nikon-value',
    ],
)
def test_detect_base_url_returns_empty_for_unparsable_remotes(
    monkeypatch: pytest.MonkeyPatch, remote: str
) -> None:
    _fake_git_remote(monkeypatch, remote)

    assert build.detect_base_url('') == ''


@pytest.mark.usefixtures('no_base_url_env')
def test_detect_base_url_returns_empty_when_git_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_git_remote(monkeypatch, FileNotFoundError('git'))

    assert build.detect_base_url('') == ''


@pytest.mark.usefixtures('no_base_url_env')
def test_detect_base_url_queries_git_in_the_given_project_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, Any] = {}

    def check_output(cmd: list[str], cwd: Path, text: bool) -> str:
        seen['cmd'] = cmd
        seen['cwd'] = cwd
        return 'git@github.com:ducklove/nikon-value.git\n'

    monkeypatch.setattr(build, 'subprocess', types.SimpleNamespace(check_output=check_output))

    build.detect_base_url('', project_root=tmp_path)

    assert seen['cwd'] == tmp_path
    assert seen['cmd'] == ['git', 'config', '--get', 'remote.origin.url']


# --------------------------------------------------------------------------- #
# ensure_dir / clean_output
# --------------------------------------------------------------------------- #


def test_ensure_dir_creates_nested_directories_idempotently(tmp_path: Path) -> None:
    target = tmp_path / 'a' / 'b' / 'c'

    build.ensure_dir(target)
    build.ensure_dir(target)

    assert target.is_dir()


def test_clean_output_wipes_previous_build_artifacts(tmp_path: Path) -> None:
    output = tmp_path / 'dist'
    (output / 'products').mkdir(parents=True)
    (output / 'products' / 'stale.html').write_text('old', encoding='utf-8')

    build.clean_output(output)

    assert output.is_dir()
    assert list(output.iterdir()) == []


# --------------------------------------------------------------------------- #
# copy_assets
# --------------------------------------------------------------------------- #


def test_asset_copy_plan_covers_every_exported_asset_constant() -> None:
    """상수와 복사 목록이 어긋나면 배포 사이트에서 그 에셋이 404가 된다."""
    planned = {(PROJECT_ROOT / source_rel).resolve() for source_rel, _ in build.ASSET_COPY_PLAN}

    missing = sorted(str(path) for path in LEGACY_ASSET_CONSTANTS if path.resolve() not in planned)

    assert not missing, f'ASSET_COPY_PLAN에 빠진 에셋 상수: {missing}'


def test_every_planned_asset_exists_in_the_repository() -> None:
    """목록에 오타가 있거나 파일이 삭제되면 빌드가 통째로 실패한다."""
    missing = sorted(
        source_rel for source_rel, _ in build.ASSET_COPY_PLAN if not (PROJECT_ROOT / source_rel).exists()
    )

    assert not missing, f'저장소에 없는 에셋 소스: {missing}'


def test_copy_assets_copies_every_planned_asset(tmp_path: Path) -> None:
    source_root = _make_source_root(tmp_path)
    output = tmp_path / 'dist'

    build.copy_assets(output, project_root=source_root, data_dir=_make_data_dir(tmp_path))

    for source_rel, target_rel in build.ASSET_COPY_PLAN:
        target = output / target_rel
        assert target.is_file(), f'{target_rel}이 복사되지 않았다'
        assert target.read_text(encoding='utf-8') == f'stub:{source_rel}'


def test_copy_assets_creates_the_pages_scaffolding(tmp_path: Path) -> None:
    source_root = _make_source_root(tmp_path)
    output = tmp_path / 'dist'

    build.copy_assets(output, project_root=source_root, data_dir=_make_data_dir(tmp_path))

    # Jekyll 처리를 끄는 마커와, 페이지가 채워질 디렉터리들
    assert (output / '.nojekyll').read_text(encoding='utf-8') == ''
    assert (output / 'products').is_dir()
    assert (output / 'data').is_dir()


def test_copy_assets_publishes_the_public_data_files(tmp_path: Path) -> None:
    """catalog.json과 제품 히스토리는 API 서버·관심목록 대시보드가 fetch한다."""
    source_root = _make_source_root(tmp_path)
    output = tmp_path / 'dist'

    build.copy_assets(output, project_root=source_root, data_dir=_make_data_dir(tmp_path))

    assert (output / 'data' / 'catalog.json').read_text(encoding='utf-8') == '{"categories": []}'
    assert (output / 'data' / 'products' / 'nikon-fm2.json').is_file()


def test_copy_assets_skips_data_files_that_do_not_exist_yet(tmp_path: Path) -> None:
    """수집 전이라 data/가 비어 있어도 빌드는 성공해야 한다."""
    source_root = _make_source_root(tmp_path)
    empty_data = tmp_path / 'empty-data'
    empty_data.mkdir()
    output = tmp_path / 'dist'

    build.copy_assets(output, project_root=source_root, data_dir=empty_data)

    assert (output / 'data').is_dir()
    assert not (output / 'data' / 'catalog.json').exists()
    assert not (output / 'data' / 'products').exists()


# --------------------------------------------------------------------------- #
# publish_root_site
# --------------------------------------------------------------------------- #


def _make_built_output(tmp_path: Path) -> Path:
    output = tmp_path / 'dist'
    (output / 'products').mkdir(parents=True)
    for name in build.ROOT_FILES_TO_PUBLISH:
        (output / name).write_text(f'new:{name}', encoding='utf-8')
    (output / 'products' / 'nikon-fm2.html').write_text('new page', encoding='utf-8')
    return output


def test_publish_root_site_copies_the_artifact_into_the_repository_root(tmp_path: Path) -> None:
    output = _make_built_output(tmp_path)
    root = tmp_path / 'repo'
    root.mkdir()

    build.publish_root_site(output, project_root=root)

    for name in build.ROOT_FILES_TO_PUBLISH:
        assert (root / name).read_text(encoding='utf-8') == f'new:{name}'
    assert (root / 'products' / 'nikon-fm2.html').read_text(encoding='utf-8') == 'new page'


def test_publish_root_site_replaces_stale_pages_and_removes_legacy_files(tmp_path: Path) -> None:
    output = _make_built_output(tmp_path)
    root = tmp_path / 'repo'
    (root / 'products').mkdir(parents=True)
    (root / 'products' / 'deleted-product.html').write_text('stale', encoding='utf-8')
    for name in build.LEGACY_ROOT_FILES_TO_REMOVE:
        (root / name).write_text('legacy', encoding='utf-8')

    build.publish_root_site(output, project_root=root)

    assert not (root / 'products' / 'deleted-product.html').exists()
    for name in build.LEGACY_ROOT_FILES_TO_REMOVE:
        assert not (root / name).exists()


def test_publish_root_site_skips_documents_the_build_did_not_produce(tmp_path: Path) -> None:
    """base URL이 없으면 sitemap.xml이 생성되지 않는다 — 그래도 실패하면 안 된다."""
    output = _make_built_output(tmp_path)
    (output / 'sitemap.xml').unlink()
    root = tmp_path / 'repo'
    root.mkdir()

    build.publish_root_site(output, project_root=root)

    assert not (root / 'sitemap.xml').exists()
    assert (root / 'index.html').exists()


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def _fixture_config() -> dict[str, Any]:
    return {
        'categories': [
            {
                'id': 'film-cameras',
                'name_ko': '필름 카메라',
                'name_en': 'Film Cameras',
                'products': [
                    {
                        'id': 'nikon-fm2',
                        'name_ko': '니콘 FM2',
                        'name_en': 'Nikon FM2',
                        'query': 'Nikon FM2 body',
                        'category_id': '3323',
                        'min_price': 100,
                        'max_price': 900,
                        'release_year': 1982,
                    },
                ],
            },
        ],
    }


def _fixture_catalog() -> dict[str, Any]:
    return {
        'updated': '2026-01-15',
        'exchange_rate': {
            'base': 'USD',
            'quote': 'KRW',
            'rate': 1396.55,
            'reference_date': '2026-01-14',
            'source': 'ECB reference rates',
        },
        'categories': [
            {
                'id': 'film-cameras',
                'products': [
                    {
                        'id': 'nikon-fm2',
                        'median': 320.0,
                        'mean': 331.0,
                        'min': 210.0,
                        'max': 480.0,
                        'q1': 280.0,
                        'q3': 380.0,
                        'count': 14,
                        'count_filtered': 12,
                        'samples': [],
                        'deals': [],
                    },
                ],
            },
        ],
    }


@pytest.fixture
def fixture_catalog_loaders(monkeypatch: pytest.MonkeyPatch) -> None:
    """실데이터 331제품 빌드는 느리므로 소형 픽스처 카탈로그로 갈아끼운다.

    build.py가 이름으로 임포트해 쓰는 로더 세 개만 교체하면 되고,
    나머지 경로(에셋 복사·페이지 렌더·publish)는 그대로 실행된다.
    """
    monkeypatch.setattr(build, 'load_catalog', _fixture_catalog)
    monkeypatch.setattr(build, 'load_catalog_config', _fixture_config)
    monkeypatch.setattr(build, 'load_history', lambda product_id: [])


@pytest.mark.usefixtures('fixture_catalog_loaders')
def test_main_writes_every_document_of_the_artifact(tmp_path: Path) -> None:
    source_root = _make_source_root(tmp_path)
    output = tmp_path / 'dist'

    build.main(
        ['--output', str(output), '--base-url', f'{BASE_URL}/'],
        project_root=source_root,
        data_dir=_make_data_dir(tmp_path),
    )

    for name in ('index.html', 'compare.html', 'resources.html', '404.html', 'robots.txt', 'sitemap.xml'):
        assert (output / name).is_file(), f'{name}이 생성되지 않았다'
    assert (output / 'products' / 'nikon-fm2.html').is_file()
    assert BASE_URL in (output / 'sitemap.xml').read_text(encoding='utf-8')


@pytest.mark.usefixtures('fixture_catalog_loaders')
def test_main_keeps_the_compare_page_out_of_the_sitemap(tmp_path: Path) -> None:
    """?ids= 조합이 무한하므로 비교 페이지는 색인 대상이 아니다."""
    source_root = _make_source_root(tmp_path)
    output = tmp_path / 'dist'

    build.main(
        ['--output', str(output), '--base-url', BASE_URL],
        project_root=source_root,
        data_dir=_make_data_dir(tmp_path),
    )

    assert 'compare.html' not in (output / 'sitemap.xml').read_text(encoding='utf-8')
    assert '<meta name="robots" content="noindex, follow">' in (output / 'compare.html').read_text(encoding='utf-8')
    # robots.txt로 막지는 않는다 — 막으면 크롤러가 noindex를 볼 수 없다.
    assert 'Disallow' not in (output / 'robots.txt').read_text(encoding='utf-8')


@pytest.mark.usefixtures('fixture_catalog_loaders')
def test_main_skips_the_sitemap_without_a_base_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv('SITE_BASE_URL', raising=False)
    monkeypatch.delenv('GITHUB_REPOSITORY', raising=False)
    _fake_git_remote(monkeypatch, FileNotFoundError('git'))
    source_root = _make_source_root(tmp_path)
    output = tmp_path / 'dist'

    build.main(
        ['--output', str(output)],
        project_root=source_root,
        data_dir=_make_data_dir(tmp_path),
    )

    assert (output / 'index.html').is_file()
    assert not (output / 'sitemap.xml').exists()


@pytest.mark.usefixtures('fixture_catalog_loaders')
def test_main_cleans_the_output_directory_before_rebuilding(tmp_path: Path) -> None:
    source_root = _make_source_root(tmp_path)
    output = tmp_path / 'dist'
    (output / 'products').mkdir(parents=True)
    (output / 'products' / 'deleted-product.html').write_text('stale', encoding='utf-8')

    build.main(
        ['--output', str(output), '--base-url', BASE_URL],
        project_root=source_root,
        data_dir=_make_data_dir(tmp_path),
    )

    assert not (output / 'products' / 'deleted-product.html').exists()
    assert (output / 'products' / 'nikon-fm2.html').is_file()


@pytest.mark.usefixtures('fixture_catalog_loaders')
def test_main_publishes_to_the_project_root_when_asked(tmp_path: Path) -> None:
    source_root = _make_source_root(tmp_path)
    output = tmp_path / 'dist'

    build.main(
        ['--output', str(output), '--base-url', BASE_URL, '--publish-root'],
        project_root=source_root,
        data_dir=_make_data_dir(tmp_path),
    )

    assert (source_root / 'index.html').is_file()
    assert (source_root / 'products' / 'nikon-fm2.html').is_file()
    assert (source_root / 'sitemap.xml').is_file()
