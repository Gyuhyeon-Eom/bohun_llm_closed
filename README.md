# bohun_llm_closed — 보훈심사 AI 폐쇄망 반입용 리포지토리

[bohun_ai](https://github.com/Gyuhyeon-Eom/bohun_ai) 프로토타입을 **폐쇄망에 반입해 본개발을 이어가기 위한** 리포지토리.
외부 CDN·온라인 의존이 제거된 앱 코드 전체와, USB 반입 키트(`transfer/`)를 포함한다.

## 반입 플로우 (3단계)

```
[온라인 PC]                       [USB]                [폐쇄망]
python3 transfer/download_all.py  →  transfer_out/  →  서버: python3 transfer/install_server.py
레포 폴더 복사                     →  레포 폴더       →  개발PC: transfer/INSTALL_WINDOWS.md
```

> **반입 규정 대응: 리포지토리에 .sh 파일이 없다.** 수집·설치·기동 도구는 전부 `.py`
> (download_all.py / install_server.py / verify_manifest.py / start.py)이며, USB에는 일반 파일·폴더만 담는다.

1. **수집** — 인터넷 되는 PC에서 `python3 transfer/download_all.py`
   휠(리눅스 서버용 + 윈도우 개발PC용, Python 3.12) · bge-m3 · Ollama+exaone · 폰트 · pgvector 소스가
   `transfer_out/`에 모이고 `MANIFEST.sha256`가 생성된다.
2. **반입** — 레포 폴더 + `transfer_out/`을 USB로 이동 (기본 ~10GB, 32GB USB 권장). 전체 목록·수동 항목은
   **[transfer/TRANSFER_LIST.md](transfer/TRANSFER_LIST.md)** 체크리스트 참조. 반입 후 `python3 transfer/verify_manifest.py`.
3. **설치** — 서버는 `python3 transfer/install_server.py` (검증→휠→모델→DB시드→스모크 자동),
   윈도우 개발 PC는 [transfer/INSTALL_WINDOWS.md](transfer/INSTALL_WINDOWS.md). 기동은 `python3 start.py`.

## 폐쇄망 운영 원칙

- **내부망에서는 어떤 다운로드도 없다** — 모든 다운로드는 외부망에서 `download_all.py`가 수행하고,
  내부망 설치(`install_server.py`)·기동(`start.py`)은 반입 파일만 사용한다 (단계별 근거: TRANSFER_LIST.md)

- 서버 기동 시 항상 `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1` — `start.py`가 자동 설정 (허브 접속 시도 차단)
- 라이브러리 버전은 반입 시점의 `transfer_out/wheels/*.lock.txt`가 기준 — 추가 반입도 같은 버전으로
- **PPP존 연계 기능(유사사례 원문 등 통합보훈시스템 데이터)은 내부 API 호출로 처리 예정 — 별도 구축·반입 없음**
- DB 계정 기본값(bohun/bohun)은 반입 전 교체, 통계 조회용 읽기전용 롤 분리 권장

## 앱 구성 (bohun_ai와 동일)

FastAPI + PostgreSQL(pgvector) + bge-m3 임베딩 + 로컬 LLM(Ollama/FabriX, OpenAI 호환).

```
api/         FastAPI 진입점            services/    기능별 로직 (챗봇·의결서·등급예측 등)
core/        LLM 게이트웨이·검색·그래프  ingestion/   OCR 접수 파이프라인
db/          스키마·시드·지식그래프      web/         화면 (외부 CDN 의존 없음)
mockgen/     시연용 목데이터            transfer/    ★ 폐쇄망 반입 키트
config/      전역 설정                 scripts/     세팅·적재·벤치마크
```

빠른 실행(모델 없이): `EMBED_BACKEND=hash LLM_BACKEND=mock ./.venv/bin/uvicorn api.main:app --port 8000`

기타 문서: [SETUP.md](SETUP.md)(서버 구축·벤치마크 배경 설명 — 명령 예시는 온라인 환경 기준)
