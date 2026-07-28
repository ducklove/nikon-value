"""scripts/backup_db.py — DB 백업·검증·업로드 테스트.

핵심 관심사는 셋이다.

1. **가동 중인 WAL DB를 온전히 떠 오는가.** 커밋이 `-wal` 에만 있는 상태에서
   `.db` 만 복사하면 테이블조차 없는 파일이 나온다(운영에서 실측된 실패 모드).
   그 상황을 그대로 재현해 두고, 나이브 복사는 깨지고 백업본은 멀쩡한지 본다.
2. **깨진 사본을 업로드하지 않는가.** `integrity_check` 실패와 "테이블이 통째로
   없는" 경우 모두 0이 아닌 종료 코드로 끝나고 업로드가 일어나지 않아야 한다.
3. **실패가 조용히 넘어가지 않는가.** 업로드 실패는 반드시 0이 아닌 종료 코드다.

rclone 과 네트워크는 절대 호출하지 않는다. 업로더와 명령 실행 지점(runner)이
주입 가능하므로 argv 조립까지 프로세스 없이 검증한다.
"""

from __future__ import annotations

import gzip
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts import backup_db
from scripts.backup_db import (
    EXIT_CONFIG,
    EXIT_OK,
    EXIT_SNAPSHOT,
    EXIT_UPLOAD,
    CommandResult,
    Options,
    RcloneUploader,
    UploadError,
)

NOW = datetime(2026, 7, 29, 4, 20, 0, tzinfo=UTC)
EXPECTED_NAME = "nikon_api-20260729T042000Z.db.gz"


# --------------------------------------------------------------------------- #
# 헬퍼
# --------------------------------------------------------------------------- #


def _make_wal_db(path: Path, rows: int = 20) -> sqlite3.Connection:
    """WAL 모드 DB를 만들고 **연결을 열어 둔 채로** 돌려준다.

    연결을 닫으면 SQLite 가 체크포인트를 돌려 WAL 내용을 `.db` 로 합쳐 버린다.
    "커밋이 WAL 에만 있는" 상황을 유지하려면 열어 둔 채여야 한다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, email TEXT)")
    conn.execute("CREATE TABLE favorites(user_id INTEGER, product_id TEXT)")
    for i in range(rows):
        conn.execute("INSERT INTO users(email) VALUES(?)", (f"u{i}@example.com",))
    conn.commit()
    return conn


@pytest.fixture
def live_db(tmp_path: Path) -> Iterator[tuple[Path, sqlite3.Connection]]:
    path = tmp_path / "live" / "nikon_api.db"
    conn = _make_wal_db(path)
    yield path, conn
    conn.close()


def _options(db: Path, out: Path, *extra: str) -> Options:
    """실제 CLI 파서를 통과시켜 만든다. 프로세스 환경은 읽지 않는다(env={})."""
    return backup_db.parse_args(["--db", str(db), "--out", str(out), *extra], env={})


class FakeUploader:
    """업로드·정리 호출을 기록하는 대역. 네트워크를 전혀 쓰지 않는다."""

    def __init__(self, *, upload_error: str | None = None, prune_error: str | None = None) -> None:
        self.upload_error = upload_error
        self.prune_error = prune_error
        self.uploaded: list[tuple[Path, str]] = []
        self.pruned: list[tuple[str, int, str]] = []

    def upload(self, artifact: Path, remote: str) -> None:
        self.uploaded.append((artifact, remote))
        if self.upload_error:
            raise UploadError(self.upload_error)

    def prune(self, remote: str, keep_days: int, prefix: str) -> None:
        self.pruned.append((remote, keep_days, prefix))
        if self.prune_error:
            raise UploadError(self.prune_error)


class RecordingNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def __call__(self, message: str) -> None:
        self.messages.append(message)


# --------------------------------------------------------------------------- #
# 1. WAL 스냅샷의 온전성
# --------------------------------------------------------------------------- #


def test_naive_copy_of_a_live_wal_database_loses_every_table(
    live_db: tuple[Path, sqlite3.Connection], tmp_path: Path
) -> None:
    """이 백업 스크립트가 존재하는 이유를 고정해 둔다.

    실패하면 SQLite 동작이 바뀐 것이므로, 그때는 문서의 경고도 다시 검토해야 한다.
    """
    db, _conn = live_db
    naive = tmp_path / "naive.db"
    naive.write_bytes(db.read_bytes())

    with sqlite3.connect(naive) as conn, pytest.raises(sqlite3.OperationalError, match="no such table"):
        conn.execute("SELECT COUNT(*) FROM users")


def test_backup_captures_rows_that_live_only_in_the_wal(
    live_db: tuple[Path, sqlite3.Connection], tmp_path: Path
) -> None:
    db, _conn = live_db
    out = tmp_path / "backups"

    assert backup_db.run(_options(db, out, "--no-upload"), now=NOW) == EXIT_OK

    restored = _gunzip(out / EXPECTED_NAME, tmp_path / "restored.db")
    with sqlite3.connect(restored) as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 20


def test_backup_excludes_an_uncommitted_transaction(
    live_db: tuple[Path, sqlite3.Connection], tmp_path: Path
) -> None:
    """열려 있는(미커밋) 트랜잭션은 스냅샷에 들어가면 안 된다."""
    db, _conn = live_db
    writer = sqlite3.connect(db)
    writer.execute("BEGIN")
    writer.execute("INSERT INTO users(email) VALUES('pending@example.com')")
    try:
        out = tmp_path / "backups"
        assert backup_db.run(_options(db, out, "--no-upload"), now=NOW) == EXIT_OK

        restored = _gunzip(out / EXPECTED_NAME, tmp_path / "restored.db")
        with sqlite3.connect(restored) as conn:
            emails = [row[0] for row in conn.execute("SELECT email FROM users")]
        assert "pending@example.com" not in emails
        assert len(emails) == 20
    finally:
        writer.rollback()
        writer.close()


def test_artifact_is_gzip_named_with_a_utc_timestamp(
    live_db: tuple[Path, sqlite3.Connection], tmp_path: Path
) -> None:
    db, _conn = live_db
    out = tmp_path / "backups"

    backup_db.run(_options(db, out, "--no-upload"), now=NOW)

    artifact = out / EXPECTED_NAME
    assert artifact.exists()
    assert artifact.read_bytes()[:2] == b"\x1f\x8b"  # gzip 매직
    # 압축 전 파일과 `.partial` 잔재를 남기지 않는다.
    assert sorted(p.name for p in out.iterdir()) == [EXPECTED_NAME]


def test_artifact_is_not_in_wal_mode_so_it_carries_no_sidecar_files(
    live_db: tuple[Path, sqlite3.Connection], tmp_path: Path
) -> None:
    """백업본은 journal_mode=DELETE 여야 -wal/-shm 없이 어디서든 열린다."""
    db, _conn = live_db
    out = tmp_path / "backups"
    backup_db.run(_options(db, out, "--no-upload"), now=NOW)

    restored = _gunzip(out / EXPECTED_NAME, tmp_path / "restored.db")
    with sqlite3.connect(restored) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_backup_never_writes_to_the_source_database(
    live_db: tuple[Path, sqlite3.Connection], tmp_path: Path
) -> None:
    db, _conn = live_db
    before = db.read_bytes(), (db.parent / f"{db.name}-wal").read_bytes()

    backup_db.run(_options(db, tmp_path / "backups", "--no-upload"), now=NOW)

    assert (db.read_bytes(), (db.parent / f"{db.name}-wal").read_bytes()) == before


def _gunzip(archive: Path, dest: Path) -> Path:
    with gzip.open(archive, "rb") as fin:
        dest.write_bytes(fin.read())
    return dest


# --------------------------------------------------------------------------- #
# 2. 검증 실패는 업로드로 이어지지 않는다
# --------------------------------------------------------------------------- #


def test_a_database_without_the_expected_table_fails_and_is_not_uploaded(tmp_path: Path) -> None:
    """`no such table: users` 상태를 정상 백업으로 착각하지 않는다."""
    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    uploader = FakeUploader()
    notifier = RecordingNotifier()

    code = backup_db.run(
        _options(db, tmp_path / "backups", "--remote", "gdrive:nv"),
        uploader=uploader,
        notifier=notifier,
        now=NOW,
    )

    assert code == EXIT_SNAPSHOT
    assert uploader.uploaded == []
    assert not (tmp_path / "backups" / EXPECTED_NAME).exists()
    assert any("users" in m for m in notifier.messages)


def test_a_corrupt_database_fails_integrity_check_and_is_not_uploaded(tmp_path: Path) -> None:
    db = tmp_path / "corrupt.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, email TEXT)")
    conn.execute("CREATE UNIQUE INDEX idx_email ON users(email)")
    for i in range(2000):
        conn.execute("INSERT INTO users(email) VALUES(?)", (f"u{i}@example.com",))
    conn.commit()
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    conn.close()

    # 헤더가 아니라 b-tree 페이지 안쪽을 망가뜨린다. 파일은 여전히 "DB 처럼" 열리고
    # backup() 도 페이지를 그대로 옮기므로, 잡아내는 것은 integrity_check 뿐이다.
    with db.open("r+b") as handle:
        handle.seek(page_size * 3 + 40)
        handle.write(b"\xde\xad\xbe\xef" * 40)

    uploader = FakeUploader()
    code = backup_db.run(
        _options(db, tmp_path / "backups", "--remote", "gdrive:nv"),
        uploader=uploader,
        notifier=RecordingNotifier(),
        now=NOW,
    )

    assert code == EXIT_SNAPSHOT
    assert uploader.uploaded == []
    assert list((tmp_path / "backups").iterdir()) == []


def test_an_inconsistent_database_is_rejected_even_though_it_reads_fine(tmp_path: Path) -> None:
    """`integrity_check` 가 예외 대신 **문제 목록**을 돌려주는 경우.

    페이지가 깨진 것이 아니라 데이터가 스키마와 어긋난 상태라, 파일은 정상적으로
    열리고 `SELECT COUNT(*)` 도 통한다. 즉 테이블 존재 확인만으로는 절대 못 잡는다.
    integrity_check 의 **반환값**을 실제로 읽고 있어야만 걸러진다.
    """
    db = tmp_path / "inconsistent.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, email TEXT)")
    conn.execute("INSERT INTO users(email) VALUES(NULL)")
    conn.commit()
    conn.execute("PRAGMA writable_schema=ON")
    conn.execute(
        "UPDATE sqlite_master SET sql='CREATE TABLE users(id INTEGER PRIMARY KEY, email TEXT NOT NULL)'"
        " WHERE name='users'"
    )
    conn.commit()
    conn.close()

    uploader = FakeUploader()
    notifier = RecordingNotifier()
    code = backup_db.run(
        _options(db, tmp_path / "backups", "--remote", "gdrive:nv"),
        uploader=uploader,
        notifier=notifier,
        now=NOW,
    )

    assert code == EXIT_SNAPSHOT
    assert uploader.uploaded == []
    assert any("integrity_check" in m for m in notifier.messages)


def test_verify_can_be_told_to_skip_the_table_check(tmp_path: Path) -> None:
    """스키마가 다른 DB에도 쓸 수 있도록 확인 대상 테이블을 끌 수 있다."""
    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()

    code = backup_db.run(
        _options(db, tmp_path / "backups", "--no-upload", "--expect-table", ""), now=NOW
    )

    assert code == EXIT_OK


def test_a_missing_database_is_a_config_error(tmp_path: Path) -> None:
    code = backup_db.run(_options(tmp_path / "nope.db", tmp_path / "backups", "--no-upload"), now=NOW)

    assert code == EXIT_CONFIG


# --------------------------------------------------------------------------- #
# 3. 업로드 실패는 반드시 표면화된다
# --------------------------------------------------------------------------- #


def test_upload_failure_exits_nonzero(live_db: tuple[Path, sqlite3.Connection], tmp_path: Path) -> None:
    db, _conn = live_db
    uploader = FakeUploader(upload_error="drive 인증 만료")
    notifier = RecordingNotifier()

    code = backup_db.run(
        _options(db, tmp_path / "backups", "--remote", "gdrive:nv"),
        uploader=uploader,
        notifier=notifier,
        now=NOW,
    )

    assert code == EXIT_UPLOAD
    assert any("drive 인증 만료" in m for m in notifier.messages)


def test_upload_failure_keeps_the_local_copy_for_a_manual_retry(
    live_db: tuple[Path, sqlite3.Connection], tmp_path: Path
) -> None:
    db, _conn = live_db
    out = tmp_path / "backups"

    backup_db.run(
        _options(db, out, "--remote", "gdrive:nv"),
        uploader=FakeUploader(upload_error="네트워크 없음"),
        notifier=RecordingNotifier(),
        now=NOW,
    )

    assert (out / EXPECTED_NAME).exists()


def test_upload_failure_skips_retention_pruning(
    live_db: tuple[Path, sqlite3.Connection], tmp_path: Path
) -> None:
    """원격에 못 올린 날 로컬 보관본까지 지워 버리면 사본이 한 벌도 없게 된다."""
    db, _conn = live_db
    out = tmp_path / "backups"
    out.mkdir()
    stale = out / "nikon_api-20200101T000000Z.db.gz"
    stale.write_bytes(b"old")
    uploader = FakeUploader(upload_error="타임아웃")

    backup_db.run(
        _options(db, out, "--remote", "gdrive:nv"),
        uploader=uploader,
        notifier=RecordingNotifier(),
        now=NOW,
    )

    assert stale.exists()
    assert uploader.pruned == []


def test_success_uploads_once_and_prunes_both_sides(
    live_db: tuple[Path, sqlite3.Connection], tmp_path: Path
) -> None:
    db, _conn = live_db
    out = tmp_path / "backups"
    uploader = FakeUploader()

    code = backup_db.run(
        _options(db, out, "--remote", "gdrive:nv", "--remote-keep-days", "30"),
        uploader=uploader,
        notifier=RecordingNotifier(),
        now=NOW,
    )

    assert code == EXIT_OK
    assert uploader.uploaded == [(out / EXPECTED_NAME, "gdrive:nv")]
    assert uploader.pruned == [("gdrive:nv", 30, "nikon_api")]


def test_no_upload_never_touches_the_uploader(
    live_db: tuple[Path, sqlite3.Connection], tmp_path: Path
) -> None:
    db, _conn = live_db
    uploader = FakeUploader()

    code = backup_db.run(
        _options(db, tmp_path / "backups", "--no-upload", "--remote", "gdrive:nv"),
        uploader=uploader,
        now=NOW,
    )

    assert code == EXIT_OK
    assert uploader.uploaded == []
    assert uploader.pruned == []


def test_running_without_a_remote_warns_that_the_backup_is_local_only(
    live_db: tuple[Path, sqlite3.Connection], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    db, _conn = live_db

    with caplog.at_level("WARNING"):
        code = backup_db.run(_options(db, tmp_path / "backups"), now=NOW)

    assert code == EXIT_OK
    assert any("SD카드" in record.getMessage() for record in caplog.records)


# --------------------------------------------------------------------------- #
# 4. 보관 정리
# --------------------------------------------------------------------------- #


def _touch_backup(directory: Path, stamp: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"nikon_api-{stamp}.db.gz"
    path.write_bytes(b"x")
    return path


def test_prune_local_removes_only_files_older_than_the_window(tmp_path: Path) -> None:
    old = _touch_backup(tmp_path, "20260701T000000Z")
    fresh = _touch_backup(tmp_path, "20260728T000000Z")

    removed = backup_db.prune_local(tmp_path, "nikon_api", 7, NOW)

    assert removed == [old]
    assert not old.exists()
    assert fresh.exists()


def test_prune_local_ignores_files_it_did_not_create(tmp_path: Path) -> None:
    """자동 삭제기가 남의 파일을 지우는 일은 절대 없어야 한다."""
    strangers = [
        tmp_path / "rclone.conf",
        tmp_path / "nikon_api.db.gz",
        tmp_path / "nikon_api-20260701.db.gz",
        tmp_path / "backup-20260701T000000Z.db.gz",
        tmp_path / "nikon_api-20260701T000000Z.db.gz.partial",
    ]
    for path in strangers:
        path.write_bytes(b"x")

    assert backup_db.prune_local(tmp_path, "nikon_api", 1, NOW) == []
    assert all(path.exists() for path in strangers)


def test_prune_local_is_disabled_by_a_zero_window(tmp_path: Path) -> None:
    ancient = _touch_backup(tmp_path, "20200101T000000Z")

    assert backup_db.prune_local(tmp_path, "nikon_api", 0, NOW) == []
    assert ancient.exists()


def test_prune_failure_is_reported_but_does_not_fail_the_backup(
    live_db: tuple[Path, sqlite3.Connection], tmp_path: Path
) -> None:
    """정리 실패는 '사본이 더 남는' 방향이라 백업 자체의 성패를 뒤집지 않는다.

    다만 방치하면 용량을 잠식하므로 알림에는 반드시 나타나야 한다.
    """
    db, _conn = live_db
    notifier = RecordingNotifier()

    code = backup_db.run(
        _options(db, tmp_path / "backups", "--remote", "gdrive:nv"),
        uploader=FakeUploader(prune_error="drive quota"),
        notifier=notifier,
        now=NOW,
    )

    assert code == EXIT_OK
    assert any("정리" in m and "drive quota" in m for m in notifier.messages)


# --------------------------------------------------------------------------- #
# 5. 원격 지정 검증과 rclone argv
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("remote", ["gdrive:", "gdrive:/", "gdrive", "", ":path"])
def test_validate_remote_rejects_unsafe_targets(remote: str) -> None:
    with pytest.raises(backup_db.ConfigError):
        backup_db.validate_remote(remote)


def test_validate_remote_accepts_a_named_folder() -> None:
    assert backup_db.validate_remote("gdrive:nikon-value-backup") == ("gdrive", "nikon-value-backup")


def test_a_bad_remote_fails_before_anything_is_uploaded(
    live_db: tuple[Path, sqlite3.Connection], tmp_path: Path
) -> None:
    db, _conn = live_db
    uploader = FakeUploader()

    code = backup_db.run(
        _options(db, tmp_path / "backups", "--remote", "gdrive:"),
        uploader=uploader,
        notifier=RecordingNotifier(),
        now=NOW,
    )

    assert code == EXIT_CONFIG
    assert uploader.uploaded == []


def test_rclone_copy_argv_targets_the_exact_filename_and_compares_checksums() -> None:
    argv = backup_db.rclone_copy_argv("rclone", Path("/b/a.db.gz"), "gdrive:nv", ["--bwlimit=1M"])

    assert argv == [
        "rclone",
        "copyto",
        "/b/a.db.gz",
        "gdrive:nv/a.db.gz",
        "--checksum",
        "--bwlimit=1M",
    ]


def test_rclone_delete_argv_is_bounded_by_age_and_filename_filter() -> None:
    argv = backup_db.rclone_delete_argv("rclone", "gdrive:nv", 30, "nikon_api", [])

    assert argv == [
        "rclone",
        "delete",
        "gdrive:nv",
        "--min-age",
        "30d",
        "--include",
        "nikon_api-*.db.gz",
    ]


# --------------------------------------------------------------------------- #
# 6. RcloneUploader — 프로세스 없이 러너만 주입해 검증
# --------------------------------------------------------------------------- #


class FakeRunner:
    def __init__(self, results: dict[str, CommandResult]) -> None:
        self.results = results
        self.calls: list[list[str]] = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        return self.results.get(argv[1], CommandResult(0))


def test_uploader_verifies_the_remote_copy_after_a_successful_exit_code(tmp_path: Path) -> None:
    artifact = tmp_path / "a.db.gz"
    artifact.write_bytes(b"12345")
    runner = FakeRunner({"lsjson": CommandResult(0, '[{"Name":"a.db.gz","Size":5}]')})

    RcloneUploader(runner=runner).upload(artifact, "gdrive:nv")

    assert [call[1] for call in runner.calls] == ["copyto", "lsjson"]


def test_uploader_rejects_an_exit_zero_that_left_nothing_on_the_remote(tmp_path: Path) -> None:
    artifact = tmp_path / "a.db.gz"
    artifact.write_bytes(b"12345")
    runner = FakeRunner({"lsjson": CommandResult(0, "[]")})

    with pytest.raises(UploadError, match="원격에 파일이 없다"):
        RcloneUploader(runner=runner).upload(artifact, "gdrive:nv")


def test_uploader_rejects_a_size_mismatch(tmp_path: Path) -> None:
    artifact = tmp_path / "a.db.gz"
    artifact.write_bytes(b"12345")
    runner = FakeRunner({"lsjson": CommandResult(0, '[{"Name":"a.db.gz","Size":0}]')})

    with pytest.raises(UploadError, match="크기가 다르다"):
        RcloneUploader(runner=runner).upload(artifact, "gdrive:nv")


def test_uploader_surfaces_a_nonzero_rclone_exit(tmp_path: Path) -> None:
    artifact = tmp_path / "a.db.gz"
    artifact.write_bytes(b"12345")
    runner = FakeRunner({"copyto": CommandResult(1, "", "Failed to copy: token expired")})

    with pytest.raises(UploadError, match="token expired"):
        RcloneUploader(runner=runner).upload(artifact, "gdrive:nv")


def test_uploader_explains_a_missing_rclone_binary(tmp_path: Path) -> None:
    artifact = tmp_path / "a.db.gz"
    artifact.write_bytes(b"12345")

    def exploding_runner(argv):
        raise FileNotFoundError("rclone")

    with pytest.raises(UploadError, match="rclone 이 설치돼"):
        RcloneUploader(runner=exploding_runner).upload(artifact, "gdrive:nv")


def test_dry_run_uploader_touches_nothing(
    live_db: tuple[Path, sqlite3.Connection], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """--dry-run 은 스냅샷까지만 만들고 원격도 로컬 보관본도 건드리지 않는다.

    그리고 하지도 않은 업로드를 "완료"라고 말하지 않는다. 이 스크립트의 존재
    이유가 "안 된 일을 됐다고 믿게 만들지 않는 것"이다.
    """
    db, _conn = live_db
    out = tmp_path / "backups"
    out.mkdir()
    ancient = _touch_backup(out, "20200101T000000Z")

    with caplog.at_level("INFO"):
        code = backup_db.run(
            backup_db.parse_args(
                ["--db", str(db), "--out", str(out), "--remote", "gdrive:nv", "--dry-run"], env={}
            ),
            now=NOW,
        )

    assert code == EXIT_OK
    assert (out / EXPECTED_NAME).exists()
    assert ancient.exists()
    assert not any("업로드 완료" in record.getMessage() for record in caplog.records)


# --------------------------------------------------------------------------- #
# 7. 설정 읽기
# --------------------------------------------------------------------------- #


def test_settings_come_from_the_environment_when_no_flag_is_given() -> None:
    options = backup_db.parse_args(
        [],
        env={
            "NIKON_BACKUP_DB_PATH": "/var/lib/nikon-value/nikon_api.db",
            "NIKON_BACKUP_DIR": "/var/lib/nikon-value-backup",
            "NIKON_BACKUP_REMOTE": "gdrive:nv",
            "NIKON_BACKUP_KEEP_DAYS": "3",
            "NIKON_BACKUP_REMOTE_KEEP_DAYS": "90",
            "NIKON_BACKUP_RCLONE_ARGS": "--bwlimit=1M --transfers=1",
        },
    )

    assert options.db == Path("/var/lib/nikon-value/nikon_api.db")
    assert options.out == Path("/var/lib/nikon-value-backup")
    assert options.remote == "gdrive:nv"
    assert options.keep_days == 3
    assert options.remote_keep_days == 90
    assert options.rclone_args == ["--bwlimit=1M", "--transfers=1"]


def test_a_flag_beats_the_environment() -> None:
    options = backup_db.parse_args(
        ["--keep-days", "14"],
        env={"NIKON_BACKUP_DB_PATH": "/db", "NIKON_BACKUP_DIR": "/out", "NIKON_BACKUP_KEEP_DAYS": "3"},
    )

    assert options.keep_days == 14


def test_db_path_falls_back_to_the_servers_own_variable() -> None:
    """운영자가 server/.env 의 DB_PATH 를 그대로 넘겨도 동작하게 둔다."""
    options = backup_db.parse_args([], env={"DB_PATH": "/db", "NIKON_BACKUP_DIR": "/out"})

    assert options.db == Path("/db")


def test_missing_required_settings_raise_a_config_error() -> None:
    with pytest.raises(backup_db.ConfigError):
        backup_db.parse_args([], env={})


def test_a_non_numeric_retention_window_is_rejected() -> None:
    with pytest.raises(backup_db.ConfigError):
        backup_db.parse_args([], env={"NIKON_BACKUP_DB_PATH": "/db", "NIKON_BACKUP_DIR": "/o",
                                      "NIKON_BACKUP_KEEP_DAYS": "일주일"})


# --------------------------------------------------------------------------- #
# 8. 알림
# --------------------------------------------------------------------------- #


def test_success_is_quiet_by_default(live_db: tuple[Path, sqlite3.Connection], tmp_path: Path) -> None:
    """매일 오는 '성공' 알림은 곧 무시되는 알림이 된다. 기본은 조용히 둔다."""
    db, _conn = live_db
    notifier = RecordingNotifier()

    backup_db.run(
        _options(db, tmp_path / "backups", "--remote", "gdrive:nv"),
        uploader=FakeUploader(),
        notifier=notifier,
        now=NOW,
    )

    assert notifier.messages == []


def test_success_can_be_announced_on_request(
    live_db: tuple[Path, sqlite3.Connection], tmp_path: Path
) -> None:
    db, _conn = live_db
    notifier = RecordingNotifier()

    backup_db.run(
        _options(db, tmp_path / "backups", "--remote", "gdrive:nv", "--notify-on-success"),
        uploader=FakeUploader(),
        notifier=notifier,
        now=NOW,
    )

    assert len(notifier.messages) == 1
    assert EXPECTED_NAME in notifier.messages[0]
    assert "users 20행" in notifier.messages[0]


def test_a_broken_notifier_never_changes_the_exit_code(
    live_db: tuple[Path, sqlite3.Connection], tmp_path: Path
) -> None:
    """알림 채널이 죽었다고 해서 성공한 백업이 실패로 둔갑하면 안 된다."""
    db, _conn = live_db

    def exploding(message: str) -> None:
        raise RuntimeError("telegram down")

    code = backup_db.run(
        _options(db, tmp_path / "backups", "--remote", "gdrive:nv", "--notify-on-success"),
        uploader=FakeUploader(),
        notifier=exploding,
        now=NOW,
    )

    assert code == EXIT_OK


def test_telegram_notifier_posts_to_the_bot_api() -> None:
    sent: list[tuple[str, bytes]] = []

    def fake_opener(request, timeout=None):
        sent.append((request.full_url, request.data))
        return None

    notify = backup_db.make_telegram_notifier(
        "123:TOKEN", "42", api_base="https://api.example.test", opener=fake_opener
    )
    notify("백업 실패")

    url, data = sent[0]
    assert url == "https://api.example.test/bot123:TOKEN/sendMessage"
    assert b"chat_id=42" in data


def test_telegram_notifier_swallows_transport_errors() -> None:
    def exploding_opener(request, timeout=None):
        raise OSError("network unreachable")

    notify = backup_db.make_telegram_notifier("t", "1", opener=exploding_opener)

    notify("백업 실패")  # 예외가 새어 나오면 실패


def test_notifier_from_env_needs_both_token_and_chat_id() -> None:
    assert backup_db.notifier_from_env({}) is backup_db.null_notifier
    assert backup_db.notifier_from_env({"NIKON_BACKUP_TELEGRAM_BOT_TOKEN": "t"}) is backup_db.null_notifier
    assert (
        backup_db.notifier_from_env(
            {"NIKON_BACKUP_TELEGRAM_BOT_TOKEN": "t", "NIKON_BACKUP_TELEGRAM_CHAT_ID": "1"}
        )
        is not backup_db.null_notifier
    )
