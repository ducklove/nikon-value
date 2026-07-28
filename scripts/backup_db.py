#!/usr/bin/env python3
"""nikon_api.db 온라인 백업 → 압축 → 원격 사본(rclone) 업로드.

이 DB에는 사용자 계정·관심목록·가격알림·텔레그램 연동이 들어 있고 `.gitignore` 로
저장소에 없는 유일본이다. SD카드 하나가 죽으면 그대로 끝나므로, 같은 카드 안의
로컬 사본만으로는 백업이라고 부를 수 없다.

설계 요점
---------
1. **`cp` 를 쓰지 않는다.** DB는 WAL 모드(`PRAGMA journal_mode=WAL`)라 최근 커밋이
   `-wal` 파일에만 있을 수 있다. 가동 중에 `.db` 만 복사하면 테이블조차 없는
   (`no such table: users`) 파일이 나온다 — 실측으로 확인된 사실이다.
   `sqlite3.Connection.backup()` 은 잠금을 존중하며 일관된 스냅샷을 만들므로
   서버를 멈추지 않아도 된다.
2. **검증한 뒤에만 업로드한다.** `PRAGMA integrity_check` 와 필수 테이블 존재를
   확인해, 깨진 사본을 정상 백업인 것처럼 원격에 쌓아 두는 사고를 막는다.
3. **실패는 반드시 표면화한다.** "백업이 있다고 믿는데 실은 없는 것"이 최악이다.
   실패는 0이 아닌 종료 코드(= systemd 에서 unit failed)로 남기고, 설정돼 있으면
   텔레그램으로도 알린다.
4. **의존성은 표준 라이브러리뿐이다.** 업로드는 외부 명령(rclone)에 위임하고 그
   실행 지점을 주입 가능하게 만들어, 테스트가 네트워크를 건드리지 않는다.
   `server/**` 는 임포트하지 않는다 — 자세한 근거는 docs/backup-restore.md 참고.

종료 코드
---------
  0  성공
  1  설정/사용법 오류 (DB 경로 없음, 원격 지정 오류 등)
  2  스냅샷 생성 또는 검증 실패 — **업로드하지 않는다**
  3  업로드 실패, 또는 업로드 후 원격 확인 실패

보관본 정리(로컬/원격)의 실패는 종료 코드를 바꾸지 않는다. 정리 실패는 "사본이 더
오래 남는" 방향이라 데이터 안전 관점에서는 안전한 쪽의 실패이기 때문이다. 다만
방치하면 용량을 잠식하므로 ERROR 로그와 알림으로 남긴다.

사용 예
-------
    python scripts/backup_db.py \\
        --db /var/lib/nikon-value/nikon_api.db \\
        --out /var/backups/nikon-value \\
        --remote gdrive:nikon-value-backup

    # 네트워크를 건드리지 않고 설정만 점검
    python scripts/backup_db.py --db ... --out ... --remote ... --dry-run
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import re
import shlex
import shutil
import socket
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

log = logging.getLogger("backup_db")

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_SNAPSHOT = 2
EXIT_UPLOAD = 3

DEFAULT_PREFIX = "nikon_api"
DEFAULT_KEEP_DAYS = 7
DEFAULT_REMOTE_KEEP_DAYS = 30
DEFAULT_EXPECT_TABLE = "users"
DEFAULT_RCLONE = "rclone"

# 파일명의 타임스탬프는 항상 UTC 다. 파이의 로컬 시간대나 서머타임과 무관하게
# 이름만으로 정렬·비교가 되고, 보관 정리도 파일명만 보고 판단할 수 있다.
STAMP_FORMAT = "%Y%m%dT%H%M%SZ"

SUFFIX = ".db.gz"
# 압축 중인 파일에 붙이는 꼬리표. 완성된 뒤에야 os.replace 로 최종 이름이 되므로,
# 중간에 죽어도 반쪽짜리 gz 가 "정상 백업"으로 취급되는 일이 없다.
PARTIAL_SUFFIX = ".partial"

TELEGRAM_TIMEOUT = 10


class BackupError(Exception):
    """종료 코드를 동반하는 실패."""

    exit_code = EXIT_CONFIG


class ConfigError(BackupError):
    exit_code = EXIT_CONFIG


class SnapshotError(BackupError):
    exit_code = EXIT_SNAPSHOT


class UploadError(BackupError):
    exit_code = EXIT_UPLOAD


# --------------------------------------------------------------------------- #
# 외부 명령 실행 (주입 가능)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


Runner = Callable[[Sequence[str]], CommandResult]


def subprocess_runner(argv: Sequence[str]) -> CommandResult:
    """실제 프로세스 실행. 테스트에서는 이 자리에 가짜 러너를 주입한다."""
    # argv 는 코드가 조립하며 셸을 거치지 않는다(shell=False). 사용자 입력이 그대로
    # 명령이 되는 경로가 없다.
    proc = subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        check=False,
    )
    return CommandResult(proc.returncode, proc.stdout, proc.stderr)


# --------------------------------------------------------------------------- #
# 스냅샷 / 검증 / 압축
# --------------------------------------------------------------------------- #


def _read_only_uri(path: Path) -> str:
    """sqlite3 read-only URI. 백업 작업이 원본을 절대 수정하지 못하게 못박는다."""
    return f"{path.resolve().as_uri()}?mode=ro"


def create_snapshot(db_path: Path, dest: Path) -> None:
    """가동 중인 DB의 일관된 사본을 dest 에 만든다.

    원본은 read-only 로 연다. 백업 작업이 실수로도 운영 DB를 건드릴 수 없게
    하기 위해서다(WAL 읽기에는 같은 디렉터리에 `-shm` 을 만들 수 있어야 하므로
    **DB가 있는 디렉터리**에는 여전히 쓰기 권한이 필요하다 — systemd 의
    `StateDirectory=` 가 그 권한을 준다).

    사본은 마지막에 journal_mode=DELETE 로 되돌린다. 백업 산출물이 WAL 헤더를
    달고 있으면 나중에 열 때마다 `-wal`/`-shm` 을 만들려 들어 읽기 전용 매체나
    다른 장비에서 다루기 번거롭다. 복원한 DB는 서버가 기동할 때 `init_db()` 가
    다시 WAL 로 바꾸므로 잃는 것이 없다.
    """
    src = sqlite3.connect(_read_only_uri(db_path), uri=True)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE")
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()


def verify_snapshot(path: Path, expect_table: str | None) -> int | None:
    """스냅샷을 검증하고, expect_table 이 있으면 그 행 수를 돌려준다.

    `integrity_check` 만으로는 "cp 로 떠서 테이블이 통째로 없는 파일"을 잡지
    못한다 — 빈 DB도 무결성은 ok 이기 때문이다. 그래서 실제 운영 테이블의
    존재까지 확인한다. 행 수는 로그·알림에 넣어 운영자가 눈으로도 이상을
    감지할 수 있게 한다.
    """
    try:
        conn = sqlite3.connect(_read_only_uri(path), uri=True)
    except sqlite3.Error as exc:
        raise SnapshotError(f"스냅샷을 열 수 없다: {exc}") from exc
    try:
        try:
            report = [row[0] for row in conn.execute("PRAGMA integrity_check").fetchall()]
        except sqlite3.DatabaseError as exc:
            raise SnapshotError(f"integrity_check 실행 실패: {exc}") from exc
        if report != ["ok"]:
            raise SnapshotError("integrity_check 실패: " + "; ".join(report[:5]))

        if not expect_table:
            return None
        try:
            row = conn.execute(f'SELECT COUNT(*) FROM "{expect_table}"').fetchone()
        except sqlite3.DatabaseError as exc:
            raise SnapshotError(
                f"필수 테이블 '{expect_table}' 를 읽을 수 없다: {exc} "
                "(가동 중 .db 만 복사했을 때 나타나는 전형적인 증상이다)"
            ) from exc
        return int(row[0])
    finally:
        conn.close()


def compress(src: Path, dest: Path) -> None:
    """gzip 압축. 완성 전까지는 `.partial` 이름을 쓴다."""
    partial = dest.with_name(dest.name + PARTIAL_SUFFIX)
    with src.open("rb") as fin, gzip.open(partial, "wb", compresslevel=6) as fout:
        shutil.copyfileobj(fin, fout, length=1024 * 1024)
    os.replace(partial, dest)


# --------------------------------------------------------------------------- #
# 보관 정리
# --------------------------------------------------------------------------- #


def artifact_pattern(prefix: str) -> re.Pattern[str]:
    """이 스크립트가 만든 파일만 매칭한다.

    자동 삭제기가 자기가 만들지 않은 파일을 지우는 일이 절대 없도록, 파일명
    형식을 엄격히 검사한 것만 삭제 후보로 삼는다.
    """
    return re.compile(rf"^{re.escape(prefix)}-(?P<stamp>\d{{8}}T\d{{6}}Z){re.escape(SUFFIX)}$")


def artifact_name(prefix: str, stamp: datetime) -> str:
    return f"{prefix}-{stamp.strftime(STAMP_FORMAT)}{SUFFIX}"


def prune_local(directory: Path, prefix: str, keep_days: int, now: datetime) -> list[Path]:
    """keep_days 보다 오래된 로컬 백업을 지우고 지운 목록을 돌려준다.

    파일 mtime 이 아니라 **파일명의 UTC 타임스탬프**를 기준으로 판단한다. 복사·
    이동으로 mtime 이 바뀌어도 판단이 흔들리지 않고, 형식이 맞지 않는 파일은
    애초에 후보가 되지 않는다. keep_days <= 0 이면 정리를 하지 않는다.
    """
    if keep_days <= 0:
        return []
    pattern = artifact_pattern(prefix)
    cutoff = now - timedelta(days=keep_days)
    removed: list[Path] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        match = pattern.match(path.name)
        if not match:
            continue
        stamp = datetime.strptime(match.group("stamp"), STAMP_FORMAT).replace(tzinfo=UTC)
        if stamp < cutoff:
            path.unlink()
            removed.append(path)
    return removed


# --------------------------------------------------------------------------- #
# 원격 업로드 (rclone)
# --------------------------------------------------------------------------- #


def validate_remote(remote: str) -> tuple[str, str]:
    """`remote:path` 형식을 검사하고 (원격이름, 경로)로 쪼갠다.

    경로가 빈 `gdrive:` 는 거부한다. 그 값이 그대로 보관 정리(`rclone delete`)에
    쓰이면 드라이브 루트 전체를 훑게 되기 때문이다. 자동으로 파일을 지우는
    기능인 만큼, 설정 오타가 사고로 이어지지 않도록 입구에서 막는다.
    """
    name, sep, path = remote.partition(":")
    if not sep or not name:
        raise ConfigError(f"원격 지정이 'remote:경로' 형식이 아니다: {remote!r}")
    if not path.strip("/"):
        raise ConfigError(
            f"원격 경로가 비어 있다: {remote!r}. 루트가 아니라 전용 폴더를 지정할 것 "
            "(예: gdrive:nikon-value-backup). 보관 정리가 루트 전체를 대상으로 도는 것을 막기 위함이다."
        )
    return name, path.strip("/")


def rclone_copy_argv(binary: str, artifact: Path, remote: str, extra: Sequence[str]) -> list[str]:
    return [
        binary,
        "copyto",
        str(artifact),
        f"{remote.rstrip('/')}/{artifact.name}",
        # 크기·시각이 아니라 해시로 비교하게 한다. 구글 드라이브는 MD5 를 주므로
        # "올라갔다고 했는데 내용이 다른" 경우를 서버 쪽에서 걸러 준다.
        "--checksum",
        *extra,
    ]


def rclone_lsjson_argv(binary: str, remote_file: str, extra: Sequence[str]) -> list[str]:
    return [binary, "lsjson", remote_file, *extra]


def rclone_delete_argv(binary: str, remote: str, keep_days: int, prefix: str, extra: Sequence[str]) -> list[str]:
    return [
        binary,
        "delete",
        remote,
        "--min-age",
        f"{keep_days}d",
        # 이 스크립트가 만든 파일만 지운다. 같은 폴더에 다른 것을 넣어 두어도 안전하다.
        "--include",
        f"{prefix}-*{SUFFIX}",
        *extra,
    ]


class RcloneUploader:
    """rclone 을 외부 명령으로 호출하는 업로더.

    파이썬 의존성을 하나도 늘리지 않는다. 러너를 주입할 수 있으므로 테스트는
    네트워크는 물론 rclone 바이너리도 필요로 하지 않는다.
    """

    def __init__(
        self,
        binary: str = DEFAULT_RCLONE,
        *,
        runner: Runner = subprocess_runner,
        extra_args: Sequence[str] = (),
    ) -> None:
        self.binary = binary
        self.runner = runner
        self.extra_args = list(extra_args)

    def _run(self, argv: Sequence[str], what: str) -> CommandResult:
        log.debug("%s: %s", what, " ".join(argv))
        try:
            result = self.runner(argv)
        except OSError as exc:
            raise UploadError(f"{what} 실행 실패: {exc} (rclone 이 설치돼 있는지 확인할 것)") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            tail = " / ".join(detail[-3:]) if detail else "(출력 없음)"
            raise UploadError(f"{what} 실패 (exit={result.returncode}): {tail}")
        return result

    def upload(self, artifact: Path, remote: str) -> None:
        """업로드하고, 원격에 실제로 같은 크기로 존재하는지까지 확인한다.

        종료 코드 0 만 믿지 않는다. 경로 오타로 엉뚱한 곳에 올라가거나 0바이트가
        올라가는 경우를 잡으려면 올린 뒤에 되읽어 보는 수밖에 없다.
        """
        self._run(rclone_copy_argv(self.binary, artifact, remote, self.extra_args), "rclone copyto")

        remote_file = f"{remote.rstrip('/')}/{artifact.name}"
        result = self._run(rclone_lsjson_argv(self.binary, remote_file, self.extra_args), "rclone lsjson")
        try:
            entries = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise UploadError(f"원격 확인 응답을 해석할 수 없다: {exc}") from exc
        if not entries:
            raise UploadError(f"업로드는 성공했다고 하는데 원격에 파일이 없다: {remote_file}")

        expected = artifact.stat().st_size
        actual = entries[0].get("Size")
        if actual != expected:
            raise UploadError(f"원격 파일 크기가 다르다: {remote_file} (로컬 {expected}B / 원격 {actual}B)")

    def prune(self, remote: str, keep_days: int, prefix: str) -> None:
        if keep_days <= 0:
            return
        self._run(
            rclone_delete_argv(self.binary, remote, keep_days, prefix, self.extra_args),
            "rclone delete",
        )


class DryRunUploader:
    """실행할 명령만 로그로 남기고 아무것도 하지 않는다 (`--dry-run`)."""

    def __init__(self, binary: str = DEFAULT_RCLONE, *, extra_args: Sequence[str] = ()) -> None:
        self.binary = binary
        self.extra_args = list(extra_args)

    def upload(self, artifact: Path, remote: str) -> None:
        log.info("[dry-run] %s", " ".join(rclone_copy_argv(self.binary, artifact, remote, self.extra_args)))

    def prune(self, remote: str, keep_days: int, prefix: str) -> None:
        if keep_days <= 0:
            return
        log.info(
            "[dry-run] %s",
            " ".join(rclone_delete_argv(self.binary, remote, keep_days, prefix, self.extra_args)),
        )


# --------------------------------------------------------------------------- #
# 운영자 알림
# --------------------------------------------------------------------------- #

Notifier = Callable[[str], None]


def null_notifier(message: str) -> None:
    """알림 채널이 설정되지 않았을 때. 로그에는 남으므로 journal 에서는 보인다."""
    log.debug("알림 채널 미설정 — 메시지를 보내지 않는다: %s", message)


def make_telegram_notifier(
    token: str,
    chat_id: str,
    *,
    api_base: str = "https://api.telegram.org",
    opener: Callable[..., object] = urllib.request.urlopen,
) -> Notifier:
    """텔레그램 sendMessage 로 알리는 notifier 를 만든다.

    `server/telegram.py` 를 임포트하지 않고 같은 채널(= 같은 봇)만 재사용한다.
    그쪽 모듈은 임포트 시점에 `server/config.py` 를 끌어와 JWT_SECRET_KEY 를
    검증하고, chat_id 를 **백업 대상 DB에서** 읽는다. 즉 DB가 깨졌거나 설정이
    잘못된 바로 그 순간에 알림 경로도 같이 죽는다. 백업 도구는 백업 대상보다
    고장에 강해야 하므로 표준 라이브러리로 20줄을 직접 쓰는 쪽을 택했다.

    발송 실패가 백업 결과를 뒤집지는 않는다(종료 코드는 백업 성패로만 결정된다).
    봇 토큰은 URL 에 들어가므로 예외 메시지·URL 을 로그에 그대로 남기지 않는다.
    """

    url = f"{api_base.rstrip('/')}/bot{token}/sendMessage"

    def send(message: str) -> None:
        payload = urllib.parse.urlencode(
            {"chat_id": chat_id, "text": message, "disable_web_page_preview": "true"}
        ).encode("utf-8")
        request = urllib.request.Request(url, data=payload, method="POST")
        try:
            opener(request, timeout=TELEGRAM_TIMEOUT)
        except urllib.error.HTTPError as exc:
            log.warning("텔레그램 알림 실패 (status=%s)", exc.code)
        except Exception as exc:  # 네트워크 오류 등 — 토큰이 새지 않도록 타입명만 남긴다
            log.warning("텔레그램 알림 실패 (%s)", type(exc).__name__)

    return send


def notifier_from_env(env: dict[str, str]) -> Notifier:
    token = env.get("NIKON_BACKUP_TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = env.get("NIKON_BACKUP_TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return null_notifier
    return make_telegram_notifier(
        token,
        chat_id,
        api_base=env.get("NIKON_BACKUP_TELEGRAM_API_BASE", "https://api.telegram.org"),
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


@dataclass
class Options:
    db: Path
    out: Path
    remote: str = ""
    prefix: str = DEFAULT_PREFIX
    keep_days: int = DEFAULT_KEEP_DAYS
    remote_keep_days: int = DEFAULT_REMOTE_KEEP_DAYS
    expect_table: str = DEFAULT_EXPECT_TABLE
    rclone: str = DEFAULT_RCLONE
    rclone_args: list[str] = field(default_factory=list)
    upload: bool = True
    dry_run: bool = False
    notify_on_success: bool = False
    verbose: bool = False


def _env_int(env: dict[str, str], key: str, default: int) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} 는 정수여야 한다: {raw!r}") from exc


def parse_args(argv: Sequence[str] | None = None, env: dict[str, str] | None = None) -> Options:
    """인자와 환경변수에서 설정을 읽는다. 자격증명·경로는 절대 하드코딩하지 않는다.

    우선순위는 명령행 인자 > 환경변수 > 기본값이다. systemd 유닛은 환경변수
    (`EnvironmentFile=`)로 넘기고, 손으로 돌릴 때는 인자로 덮어쓸 수 있다.
    """
    env = dict(os.environ if env is None else env)

    parser = argparse.ArgumentParser(
        prog="backup_db.py",
        description="SQLite DB를 무중단으로 백업·검증·압축하고 원격(rclone)에 올린다.",
    )
    parser.add_argument("--db", default=env.get("NIKON_BACKUP_DB_PATH") or env.get("DB_PATH"),
                        help="백업할 DB 경로 (환경변수 NIKON_BACKUP_DB_PATH 또는 DB_PATH)")
    parser.add_argument("--out", default=env.get("NIKON_BACKUP_DIR"),
                        help="로컬 백업 디렉터리 (환경변수 NIKON_BACKUP_DIR)")
    parser.add_argument("--remote", default=env.get("NIKON_BACKUP_REMOTE", ""),
                        help="rclone 원격 경로 'remote:폴더' (환경변수 NIKON_BACKUP_REMOTE)")
    parser.add_argument("--prefix", default=env.get("NIKON_BACKUP_PREFIX", DEFAULT_PREFIX),
                        help=f"백업 파일명 접두사 (기본 {DEFAULT_PREFIX})")
    parser.add_argument("--keep-days", type=int, default=_env_int(env, "NIKON_BACKUP_KEEP_DAYS", DEFAULT_KEEP_DAYS),
                        help=f"로컬 보관 일수, 0이면 정리하지 않음 (기본 {DEFAULT_KEEP_DAYS})")
    parser.add_argument("--remote-keep-days", type=int,
                        default=_env_int(env, "NIKON_BACKUP_REMOTE_KEEP_DAYS", DEFAULT_REMOTE_KEEP_DAYS),
                        help=f"원격 보관 일수, 0이면 정리하지 않음 (기본 {DEFAULT_REMOTE_KEEP_DAYS})")
    parser.add_argument("--expect-table", default=env.get("NIKON_BACKUP_EXPECT_TABLE", DEFAULT_EXPECT_TABLE),
                        help="검증 시 존재를 확인할 테이블. 빈 문자열이면 확인하지 않음")
    parser.add_argument("--rclone", default=env.get("NIKON_BACKUP_RCLONE", DEFAULT_RCLONE),
                        help="rclone 실행 파일 경로")
    parser.add_argument("--rclone-arg", action="append", default=[], dest="rclone_arg",
                        help="rclone 에 덧붙일 인자 (반복 가능). 예: --rclone-arg=--bwlimit=1M")
    parser.add_argument("--no-upload", action="store_true", help="로컬 백업만 만들고 원격 업로드를 건너뛴다")
    parser.add_argument("--dry-run", action="store_true",
                        help="스냅샷·검증까지만 하고 업로드/삭제는 명령만 출력한다")
    parser.add_argument("--notify-on-success", action="store_true", help="성공했을 때도 알림을 보낸다")
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.db:
        raise ConfigError("--db 또는 NIKON_BACKUP_DB_PATH 가 필요하다")
    if not args.out:
        raise ConfigError("--out 또는 NIKON_BACKUP_DIR 이 필요하다")

    extra = shlex.split(env.get("NIKON_BACKUP_RCLONE_ARGS", "")) + list(args.rclone_arg)

    return Options(
        db=Path(args.db),
        out=Path(args.out),
        remote=args.remote.strip(),
        prefix=args.prefix,
        keep_days=args.keep_days,
        remote_keep_days=args.remote_keep_days,
        expect_table=args.expect_table,
        rclone=args.rclone,
        rclone_args=extra,
        upload=not args.no_upload,
        dry_run=args.dry_run,
        notify_on_success=args.notify_on_success,
        verbose=args.verbose,
    )


def _make_artifact(options: Options, now: datetime) -> tuple[Path, int | None]:
    """스냅샷 → 검증 → 압축. 중간 산출물은 어떤 경로로 끝나든 정리한다."""
    if not options.db.exists():
        raise ConfigError(f"DB 파일이 없다: {options.db}")

    options.out.mkdir(parents=True, exist_ok=True)
    # 계정 정보가 들어 있는 사본이다. 디렉터리부터 소유자 전용으로 좁힌다.
    options.out.chmod(0o700)

    name = artifact_name(options.prefix, now)
    artifact = options.out / name
    staging = options.out / f"{name[: -len(SUFFIX)]}.db"

    try:
        try:
            create_snapshot(options.db, staging)
        except sqlite3.Error as exc:
            raise SnapshotError(f"스냅샷 생성 실패: {exc}") from exc

        rows = verify_snapshot(staging, options.expect_table or None)
        compress(staging, artifact)
        artifact.chmod(0o600)
    finally:
        staging.unlink(missing_ok=True)
        artifact.with_name(artifact.name + PARTIAL_SUFFIX).unlink(missing_ok=True)

    return artifact, rows


def run(
    options: Options,
    *,
    uploader: object | None = None,
    notifier: Notifier | None = None,
    now: datetime | None = None,
) -> int:
    """백업 한 번을 수행하고 종료 코드를 돌려준다."""
    now = now or datetime.now(UTC)
    notifier = notifier or null_notifier
    host = socket.gethostname()

    def notify(message: str) -> None:
        try:
            notifier(f"[nikon-value/{host}] {message}")
        except Exception as exc:  # 알림 실패가 백업 결과를 바꾸지 않는다
            log.warning("알림 발송 중 예외 (%s)", type(exc).__name__)

    remote = options.remote
    do_upload = options.upload and bool(remote)
    if do_upload:
        try:
            validate_remote(remote)
        except BackupError as exc:
            log.error("원격 설정 오류: %s", exc)
            notify(f"백업을 시작하지 못했다 — {exc}")
            return exc.exit_code
    elif options.upload and not remote:
        # 원격 사본이 백업의 존재 이유다. 조용히 로컬만 만들고 성공한 척하지 않는다.
        log.warning("원격이 지정되지 않았다(--remote / NIKON_BACKUP_REMOTE). 로컬 사본만 만든다 — "
                    "SD카드가 죽으면 이 백업도 같이 죽는다.")

    try:
        artifact, rows = _make_artifact(options, now)
    except BackupError as exc:
        log.error("백업 실패: %s", exc)
        notify(f"백업 실패 — {exc}")
        return exc.exit_code

    size = artifact.stat().st_size
    detail = f"{artifact.name} ({size:,}B"
    if rows is not None:
        detail += f", {options.expect_table} {rows:,}행"
    detail += ")"
    log.info("스냅샷 검증 통과: %s", detail)

    if uploader is None:
        if options.dry_run:
            uploader = DryRunUploader(options.rclone, extra_args=options.rclone_args)
        elif do_upload:
            uploader = RcloneUploader(options.rclone, extra_args=options.rclone_args)

    if do_upload and uploader is not None:
        try:
            uploader.upload(artifact, remote)
        except BackupError as exc:
            log.error("업로드 실패: %s", exc)
            # 로컬 사본은 일부러 남긴다. 다음 실행 때 사람이 손으로 올릴 수 있다.
            notify(f"업로드 실패 — {detail}: {exc}\n로컬 사본은 {artifact} 에 남아 있다.")
            return exc.exit_code
        if not options.dry_run:
            log.info("원격 업로드 완료: %s/%s", remote.rstrip("/"), artifact.name)

    # --- 보관 정리 -------------------------------------------------------- #
    # 여기서부터의 실패는 종료 코드를 바꾸지 않는다. 사본이 더 남는 방향의
    # 실패라 데이터가 위험해지지 않기 때문이다. 대신 ERROR + 알림으로 남긴다.
    warnings: list[str] = []

    if options.dry_run:
        log.info("[dry-run] 로컬 정리 대상: %d일 이전", options.keep_days)
    else:
        try:
            removed = prune_local(options.out, options.prefix, options.keep_days, now)
            if removed:
                log.info("로컬 보관본 %d개 정리 (%d일 초과)", len(removed), options.keep_days)
        except OSError as exc:
            log.error("로컬 보관본 정리 실패: %s", exc)
            warnings.append(f"로컬 정리 실패: {exc}")

    if do_upload and uploader is not None:
        try:
            uploader.prune(remote, options.remote_keep_days, options.prefix)
        except BackupError as exc:
            log.error("원격 보관본 정리 실패: %s", exc)
            warnings.append(f"원격 정리 실패: {exc}")

    if warnings:
        notify("백업은 성공했으나 보관 정리에 실패했다 — " + "; ".join(warnings))
    elif options.notify_on_success:
        where = f" → {remote}" if do_upload else " (로컬 전용)"
        notify(f"백업 성공 — {detail}{where}")

    return EXIT_OK


def main(argv: Sequence[str] | None = None, **kwargs: object) -> int:
    try:
        options = parse_args(argv)
    except BackupError as exc:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
        log.error("설정 오류: %s", exc)
        return exc.exit_code

    logging.basicConfig(
        level=logging.DEBUG if options.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )
    if "notifier" not in kwargs:
        kwargs["notifier"] = notifier_from_env(dict(os.environ))
    return run(options, **kwargs)  # type: ignore[arg-type]


if __name__ == "__main__":
    sys.exit(main())
