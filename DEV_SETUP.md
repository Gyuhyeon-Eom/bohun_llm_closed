# DEV_SETUP — 포맷된 맥 개발환경 세팅 가이드

포맷 직후의 macOS에서 이 프로젝트 개발환경(IntelliJ + Python + 라이브러리 + DB + 모델)을
처음부터 구축하는 절차. 대부분은 스크립트 한 방으로 끝나고, IntelliJ GUI 설정만 수동이다.

> 폐쇄망 **서버** 구축·실측은 [SETUP.md](SETUP.md) 참고. 이 문서는 **개발 PC(맥)** 용.

## 1. 원클릭 세팅

```bash
git clone <레포 주소> bohun-ai && cd bohun-ai
bash scripts/setup_dev.sh                # 기본: brew·Python·PostgreSQL·DB 시드·스모크 테스트
bash scripts/setup_dev.sh --models       # + bge-m3(~2.3GB) + Ollama exaone3.5(~4.8GB)
bash scripts/setup_dev.sh --intellij     # + IntelliJ IDEA CE 설치
```

스크립트는 멱등이라 중간에 실패해도 다시 실행하면 이어서 진행된다. 끝나면:

```bash
bash start.sh          # http://127.0.0.1:8000
```

모델 없이 UI·API만 개발할 때는 mock으로 충분하다:

```bash
EMBED_BACKEND=hash LLM_BACKEND=mock ./.venv/bin/uvicorn api.main:app --port 8000
```

## 2. 스크립트가 설치하는 것

| 항목 | 내용 | 비고 |
|---|---|---|
| Homebrew | 패키지 관리자 | 이미 있으면 스킵 |
| Python 3.12 | `.venv` 가상환경 생성 | 프로젝트 루트 `.venv/` |
| 라이브러리 | `requirements.txt` 전체 | psycopg, FlagEmbedding, fastapi, uvicorn, openpyxl, reportlab 등 |
| PostgreSQL 17 + pgvector | `bohun` DB/계정 생성, vector 확장 | 개발용 비번 `bohun` — 운영 반입 시 교체 |
| DB 스키마·시드 | schema.sql → schema_case.sql → 코드 296규칙·KCD → 목데이터 6건 → 지식그래프 | 멱등 재실행 가능 |
| Ollama | 로컬 LLM 런타임 | 모델은 `--models` 때만 |

## 3. IntelliJ 설정 (수동, 1회)

IntelliJ IDEA에서 Python을 쓰려면 **Python Community Edition 플러그인**이 필요하다.
(PyCharm을 쓴다면 플러그인 단계는 생략하고 인터프리터부터.)

1. **플러그인**: `Settings → Plugins → Marketplace`에서 "Python Community Edition" 설치 후 재시작
2. **프로젝트 열기**: `File → Open` 으로 레포 루트 선택
3. **인터프리터(SDK)**: `File → Project Structure → SDKs → + → Add Python SDK
   → Existing environment → <레포>/.venv/bin/python` 선택 후, `Project` 탭에서 이 SDK를 프로젝트 SDK로 지정
4. **실행 구성** (`Run → Edit Configurations → + → Python`):
   - Module name: `uvicorn` (Script path 대신 "Module name" 모드 선택)
   - Parameters: `api.main:app --port 8000`
   - Working directory: 레포 루트
   - Environment variables (모델 없이 개발 시): `EMBED_BACKEND=hash;LLM_BACKEND=mock`
5. **테스트 실행 구성**: 같은 방식으로 Script path에 `tests/test_smoke.py` 지정

## 4. 자주 걸리는 것 (트러블슈팅)

- **`psql: command not found`** — postgresql@17은 keg-only. `export PATH="$(brew --prefix postgresql@17)/bin:$PATH"`
  (start.sh·setup_dev.sh는 자동 처리)
- **서버 켰는데 안건 목록이 에러 카드** — PostgreSQL 미기동. `brew services start postgresql@17`
- **챗봇에 "LLM 미연결" 칩** — Ollama 미기동 또는 모델 미설치.
  `brew services start ollama && ollama pull exaone3.5:7.8b`
- **bge-m3 로드 실패(FileNotFoundError)** — `models/bge-m3` 미반입 상태.
  `--models`로 받거나, 임시로 `EMBED_BACKEND=hash`
- **임베딩 백엔드를 바꾼 뒤 검색이 이상함** — hash↔bge 벡터는 호환되지 않는다.
  시드 단계(5/7)를 해당 백엔드로 재실행해 전체 재임베딩할 것

## 5. 폐쇄망 반입 시 추가 체크 (개발 PC에서 준비)

- `pip download -r requirements.txt -d wheels/` — **반입 대상 서버와 동일 OS·아키텍처·Python 버전**에서 수행
- `models/bge-m3` 디렉토리 통째 복사 + 서버에서 `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1` 설정
- 웹 UI는 외부 CDN 의존 없음(v0.5에서 Google Fonts 제거) — 추가 확인 불필요
