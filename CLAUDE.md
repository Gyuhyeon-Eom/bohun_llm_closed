# bohun_ai — 작업 규칙

국가보훈부 「AI·빅데이터 분석 기반 보훈심사 지원 시스템」 개발 원본 리포 (선도소프트 컨소시엄).

## API 개발 계약 (260803)
- **`docs/interface/보훈심사_AI_API_명세_v0.11.xlsx` 대로 개발한다** — 프론트 측 양식(API별 시트,
  자녀 필드 들여쓰기·회색). 상세 원칙은 `docs/interface/README.md`.
- 명세의 원본은 `api/main.py` 실구현 — 코드와 문서가 어긋나면 버그. API 변경 시 명세 버전 up 후 프론트 공유.
- 응답 규약: 계약 API는 `{success, message, data}` 봉투(실패 시 success=false+message). 평문 주민번호 응답 금지.

## 표준 워크플로
모든 작업: commit → push(`claude/...` 브랜치) → PR → main 병합 → **bohun_llm_closed 동기화** → **bohun_stack 동기화**(해당 파일이 있는 경우만).

## 리포 구성
| 리포 | 역할 |
|---|---|
| bohun_ai | 개발 원본 (웹+API+파이프라인 전부) |
| bohun_llm_closed | 폐쇄망 반입 동기화본 (전체 앱) — 프론트가 붙는 API 서버 |
| bohun_stack | 파이프라인 CLI 전용 반입본 (웹/API/mockgen 없음, tests 없음) |
| pva_ai- | 대용량 반입물 (wheel·도커 이미지·패치 zip) |

## 폐쇄망 규칙
- .sh 반입 금지 — 도구는 전부 .py / 내부망 다운로드 불가(wheel·이미지 사전 반입, 마커 양플랫폼 감사)
- 임베딩: bge-m3 단일 확정 (개발은 EMBED_BACKEND=hash) / LLM 개발은 mock, 운용은 FabriX
- FabriX 실규격: `docs/FABRIX_API.md` — LLM Serving은 서비스 필터 미적용이라 Security Filter 병행 필수

## 개인정보 (절대 규칙)
- 실데이터(실명+주민번호+의료정보) GitHub 평문 업로드 금지 — openssl 암호화 또는 USB만
- 로그·런 매니페스트에 실명(마스킹: 하O길 형태)·주민번호·API 키 기록 금지
- 주민번호는 외부 API 전송 직전 `_scrub_rrn` 스크럽, 보고서 스크린샷도 마스킹

## 자주 쓰는 명령
- 전체 테스트: `EMBED_BACKEND=hash python3 -m pytest tests/ -q` (PG 필요 — 죽어 있으면
  `sudo -u postgres pg_ctlcluster 16 main start`)
- DB 스키마 갱신: `python3 scripts/apply_db_updates.py` (멱등)
- 파이프라인: `python -m pipeline ingest|decision|grade|check`
