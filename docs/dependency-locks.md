# 의존성 락파일

## 왜 필요한가

기존 `requirements.txt` 는 전부 `>=` 하한 핀이었다. CI 는 실행할 때마다 그 시점의
최신 버전을 새로 설치하므로, 우리가 아무것도 바꾸지 않아도 상류 릴리스 하나에
빌드가 깨질 수 있다. 실제로 Python 3.14 환경에서 slowapi·starlette 의 deprecation
경고가 그렇게 나타났다. 라즈베리파이에 재설치할 때도 "지난번과 같은 버전"이 보장되지
않아, 장애 복구 중에 새로운 변수를 하나 더 떠안게 된다.

그래서 **사람이 읽는 상위 선언**과 **기계가 쓰는 고정 목록**을 분리했다.

## 파일 구성

| 파일 | 성격 | 쓰는 곳 |
| --- | --- | --- |
| `scripts/requirements.txt` | 상위 선언 (수집·빌드 파이프라인) | 사람이 편집 |
| `server/requirements.txt` | 상위 선언 (API 서버) | 사람이 편집 |
| `requirements-dev.txt` | 상위 선언 (위 둘 + ruff) | 사람이 편집 |
| `scripts/requirements.lock.txt` | 고정 목록 | `update-prices.yml`, `deploy-pages.yml` |
| `server/requirements.lock.txt` | 고정 목록 | 라즈베리파이 배포 |
| `requirements-dev.lock.txt` | 고정 목록 | `ci.yml`, 로컬 개발 |

버전을 올리거나 패키지를 추가할 때는 **상위 선언만 고치고 락을 재생성**한다.
락파일을 손으로 편집하지 않는다.

락파일에는 `--hash=sha256:...` 이 함께 들어 있다. 해시가 하나라도 있으면 pip 는
자동으로 hash-checking 모드로 들어가 모든 아티팩트를 검증하고, 목록에 없는 패키지가
끼어드는 것도 거부한다. 즉 락파일 설치 명령에 다른 패키지를 덧붙이면 실패한다
(그게 의도된 동작이다).

## 설치

```bash
# 로컬 개발 / CI
pip install -r requirements-dev.lock.txt

# 라즈베리파이 (API 서버)
pip install -r server/requirements.lock.txt

# 수집 파이프라인만
pip install -r scripts/requirements.lock.txt
```

## 갱신 절차

락 생성에는 [uv](https://docs.astral.sh/uv/) 를 쓴다. 대상 Python 버전을 실제로
설치하지 않고도 그 버전 기준으로 해석할 수 있어, 개발 머신과 CI 의 파이썬이 달라도
같은 락을 만들 수 있다.

```bash
# uv 설치 (한 번만)
pip install uv          # 또는: brew install uv

cd <저장소 루트>
uv pip compile --universal --python-version 3.11 --generate-hashes --no-annotate \
    scripts/requirements.txt   -o scripts/requirements.lock.txt
uv pip compile --universal --python-version 3.11 --generate-hashes --no-annotate \
    server/requirements.txt    -o server/requirements.lock.txt
uv pip compile --universal --python-version 3.11 --generate-hashes --no-annotate \
    requirements-dev.txt       -o requirements-dev.lock.txt
```

갱신 후에는 반드시 로컬에서 확인하고 커밋한다.

```bash
python -m venv /tmp/lockcheck && /tmp/lockcheck/bin/pip install -r requirements-dev.lock.txt
/tmp/lockcheck/bin/ruff check .
JWT_SECRET_KEY=$(python -c "import secrets;print(secrets.token_urlsafe(48))") \
  /tmp/lockcheck/bin/python -m pytest -q
```

### 옵션의 의미

- `--universal` — 플랫폼마다 따로 만들지 않고, 환경 마커(`sys_platform`,
  `platform_python_implementation` 등)를 붙여 **하나의 락으로 전부 커버**한다.
  개발(macOS arm64), CI(ubuntu x86_64), 배포(라즈베리파이 linux arm) 가 모두 다르므로
  이게 없으면 락을 세 벌 관리해야 한다.
- `--python-version 3.11` — **해석 기준을 가장 낮은 지원 버전에 맞춘다.** 이 값은
  하한이며, 결과 락은 3.11 이상 어디서든 설치된다.
- `--generate-hashes` — 재현성에 더해 공급망 무결성까지 확보한다.
- `--no-annotate` — `# via ...` 주석을 빼서 diff 를 읽기 쉽게 유지한다.

### Python 3.12 / 3.14 차이를 어떻게 처리했나

- CI 는 **3.12**, 개발 컨테이너는 **3.14**, 라즈베리파이 OS(Bookworm)는 **3.11** 이다.
- 락을 3.14 에서 `pip freeze` 로 뜨면 3.11/3.12 에서 설치되지 않을 수 있다. 그래서
  실행 중인 인터프리터와 무관하게 **3.11 기준으로 해석**하도록 `--python-version` 을 준다.
- 이번 해석 결과에는 파이썬 버전에 따라 갈리는 항목이 **하나도 없었다**(마커는 모두
  플랫폼 기준). 즉 3.11 / 3.12 / 3.14 에 정확히 같은 버전이 깔린다.
- 검증도 실제 3.12 에서 했다: `uv python install 3.12` 로 3.12.13 을 받아 새 venv 에
  락을 설치하고 `ruff check` 와 전체 테스트를 돌렸다. 개발 venv(3.14)에서도 동일하게 통과한다.
- 앞으로 지원 하한이 올라가면 세 명령의 `--python-version` 을 같이 올린다.

만약 나중에 어떤 패키지가 버전별로 갈리면 락에
`pkg==1.2 ; python_full_version < '3.12'` 같은 줄이 생긴다. 정상이며 그대로 두면 된다.

## Dependabot

`.github/**` 는 이 문서의 범위 밖이라 파일을 직접 만들지 않았다. 아래 내용을
`.github/dependabot.yml` 로 추가하면 상위 선언과 락파일을 함께 갱신하는 PR 이 올라온다.

```yaml
version: 2
updates:
  # 상위 선언(*.txt)과 락파일을 함께 갱신한다.
  # pip 에코시스템은 requirements 파일을 자동 탐색하므로 경로별로 나눠 둔다.
  - package-ecosystem: pip
    directory: "/"
    schedule:
      interval: weekly
      day: monday
    open-pull-requests-limit: 5
    groups:
      # 개별 PR 이 쏟아지지 않게 묶는다. CI 가 통과하면 한 번에 머지한다.
      python-deps:
        patterns: ["*"]
    labels: ["dependencies"]

  - package-ecosystem: github-actions
    directory: "/"
    schedule:
      interval: monthly
    labels: ["dependencies"]
```

주의: Dependabot 은 상위 `.txt` 를 갱신하지만 `uv pip compile` 을 대신 돌려 주지는
않는다. 하한 핀만 올라온 PR 이라면 위의 갱신 절차를 직접 실행해 락을 다시 만들고
같은 브랜치에 얹어야 한다. 그 과정이 번거롭다면 Dependabot 은 `github-actions`
생태계에만 켜 두고, 파이썬 의존성은 분기마다 수동으로 재생성하는 편이 단순하다.
