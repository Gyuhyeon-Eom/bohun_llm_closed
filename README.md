# e보훈심사 AI 지원시스템 (bohun_ai)

보훈심사 업무(요건심사·상이등급심사)를 지원하는 AI 시스템.
이 문서는 **공동 작업자가 시스템 로직을 파악하기 위한 설명서**다 — 각 기능이 내부적으로
어떤 순서로, 어떤 파일에서, 어떤 규칙으로 동작하는지를 설명한다.

## 설계 원칙

1. **사실 조립은 결정적으로, 서술만 LLM으로.** 의결서의 사건 자료·법령·유사사례는 DB 조회와
   검색으로 조립하고(환각 불가), LLM은 마지막 문안 서술에만 사용한다.
2. **HITL(Human-in-the-loop).** AI 산출물은 전부 "추천값/초안"이며, 담당자가 확인·수정·확정해야
   효력 상태(`확정`)가 된다.
3. **백엔드 교체 가능.** LLM은 OpenAI 호환 공통 경로(Ollama ↔ FabriX 환경변수 교체),
   임베딩은 bge-m3 ↔ hash 대역을 환경변수로 스왑한다.

## 전체 데이터 흐름

```
[문서 적재]  OCR 텍스트 → 검증(저신뢰만 교정) → 청킹 → 임베딩 → PostgreSQL 적재
[질의/생성]  화면 요청 → FastAPI → (검색: dense+sparse→RRF) + (그래프 탐색) → 팩트 조립
                        → LLM 문안 생성(필요한 기능만) → 담당자 확인·확정 → 산출물(txt/pdf/xlsx)
```

저장소는 PostgreSQL 하나다: 문서 청크(+pgvector 벡터), 사건 데이터, 지식그래프, 피드백까지
같은 DB에 있고, doc_type 필터·조인이 SQL 한 방에 처리된다.

## 기능별 로직

### 1. 문서 적재 파이프라인 — `ingestion/`

`POST /ingest` 또는 `scripts/ingest_*.py` → 공통 흐름:

1. **파싱** (`api/main.py:_parse_blocks`) — OCR JSON(`{"blocks":[...]}`)이면 블록 그대로,
   일반 텍스트면 빈 줄 기준 문단 분해.
2. **검증** (`verifier.py`) — OCR confidence가 `VERIFY_CONF_THRESHOLD`(0.85) 미만인 블록만
   교정기에 투입(전량 LLM 투입은 비용상 비현실적). 원문은 `meta["ocr_raw"]`에 보존.
   현재 교정기는 규칙 기반 `RuleCorrectLLM`(안전한 역치환만) — FabriX 연결 시 같은 자리에 교체.
3. **청킹** (`chunker.py`) — 표·도표는 통째 1청크, 문단은 1,200자 슬라이딩(150자 중첩).
4. **임베딩** (`embedder.py`) — bge-m3 dense 1024차원. `EMBED_BACKEND=hash`면 해시 기반
   의사벡터(파이프라인 검증용 — 의미 검색 아님).
5. **적재** (`indexer.py`) — 문서 sha256 기준 멱등(재적재 시 스킵). `경로#태그` 형태로
   같은 파일을 다른 doc_type으로 병행 적재 가능.

### 2. 하이브리드 검색 — `core/retrieval.py`

모든 RAG 기능이 이 함수 하나(`hybrid_search`)를 쓴다:

- **dense**: pgvector 코사인 거리 상위 50
- **sparse**: tsvector 어휘 일치 상위 50. 질의를 토큰으로 쪼개 **OR 결합** —
  AND(plainto_tsquery)는 한국어 조사 변이·저품질 OCR에서 재현율이 0에 수렴(실측)
- **융합**: RRF `Σ w/(60+rank)`. 가중치는 dense 1.0 / sparse 0.5 (벤치마크 실측 기반,
  `RRF_*_WEIGHT` 환경변수로 조정)
- doc_type 필터(예: `법령`, `분과 매뉴얼`, `ui_upload`)는 같은 SQL WHERE로 처리

### 3. 지식그래프 — `db/build_graph.py`, `core/graph.py`

시드 테이블(심의체계·주문안 296규칙·KCD)을 노드/엣지로 변환해 저장:

```
review_type ─HAS_CONTENT→ review_content ─APPLIES{판단대상, 결과, 순서}→ 조문(표준문안)
case ─OF_TYPE→ review_type,  case ─HAS_KCD→ kcd
```

- `applied_clauses(심의내용, 판단대상)` → 적용 조문·주문·표준문안 체인을 **결정적으로** 수집
- `cases_by_kcd(codes)` → 같은 상이처(KCD)를 가진 과거 사례를 겹치는 코드 수 순으로 반환
- 검토서(⑤)·의결서의 "관련법령/유사사례" 근거는 전부 여기서 나온다 — LLM이 만들지 않는다

**판단기준 룰 그래프 (graph-lite, v2.4)** — `scripts/build_rule_graph.py`가 `judgment_rule`을
`jr_sub(분과) →HAS_DISEASE→ jr_disease(질환) →JUDGED_BY{조건}→ jr_axis(판단축) →REQUIRES→ jr_doc(서류)`
로 파생 적재. `rule_facts(질문)` 이 질환 매칭 → 판단축·조건·필요서류·근거를 멀티홉으로 수집해
**챗봇 컨텍스트에 결정적 근거로 주입**된다 (예: "난청 기준?" → 6분법 26dB 조건 + 청력검사 서류 + 모듈 출처).

### 4. 심의의결서 — `services/decision_doc.py` (핵심 기능)

**조립(1~3장, 결정적)** — `build_doc(app_id)`:
- 사건 데이터 로드(`load_case`): 신청 → 상이처 → 의무기록(시간순) → 병적 → 공적서류 → 기존 결론
- 적용 조문(`clauses_for`): 신분(군인/공무원)×사망 여부로 예우법/보상법 조문 결정 (분기표)
- 법령 원문·분과 판단기준: RAG 검색. 매뉴얼 발췌는 상병명 토큰(3자+)이 겹치지 않으면 버림
  (타 분과 내용 혼입 차단)
- 유사사례: 그래프 KCD 역탐색

**AI 사전 판단** — `_predict_axes()`: 두 축(국가유공자/보훈보상)의 추천값을 **규칙 점수제**로 계산:
- 유사사례 다수결(해당 다수 +1 / 비해당 다수 -1)
- 재판독 진구성 소견 -1, 급성 외상 소견 +1, 공적서류-의무기록 불일치 -1
- 점수>0 → 국가유공자 축 해당, 점수≥0 → 보훈보상 축 해당. |점수|로 신뢰도(높음/보통/낮음)
- 화면(4장)은 담당자 결론이 없을 때만 이 값을 라디오에 미리 선택하고 근거 배지를 띄운다

**문안 생성(4장, LLM)** — `draft_judgment()`: 담당자가 선택한 이원 판단 + 사건 자료 요약(`_dossier`)
+ 분과 판단모듈(`core/subcommittee_modules.py`)을 프롬프트에 넣어 판단내용 문안만 생성.
마크다운·서명 블록 등 양식 이탈은 `_strip_markdown`/`_strip_artifacts`로 제거 후
`conclusion`에 upsert(상태 `작성중`).

**확정** — `finalize()`: 담당자 수정본 반영 + 상태 `확정`. 전 상이처 확정 시 안건 상태 `의결`.

**산출물** — `_full_text()`가 1~4장 전체를 계층 들여쓰기 텍스트로 조립 →
`export_txt`(utf-8-sig)/`export_pdf`(reportlab, 시스템 한글 폰트 탐색 + CID 폴백).
- `dis_id` 지정 시 해당 상이처만 담은 **개별본**
- `export_split`: 상이처별 개별본 zip / `export_batch`: 여러 안건 일괄 zip

### 5. 상이등급 판정예측 — `services/grade_predict.py`

시행령 [별표3] 상이등급구분표 189개 기준문과 대조:
- **어휘 점수**: 질의 토큰(2자+)이 기준문에 부분문자열로 포함되면 토큰 길이만큼 가점
- **임베딩 코사인**: 동점 구간의 의미적 순위
- 부위 지정 시 해당 절 내 탐색. 1·2순위 등급 후보 + 기준 원문 + 과거 사례를 반환
- **사유문은 기준 원문을 인용해 결정적으로 조립** — 생성 LLM 불사용

### 6. 챗봇·유사사례·검토서·통계 — `services/`

- **챗봇** (`chatbot.py`): 하이브리드 검색 상위 5 → 출처 라벨 붙여 컨텍스트 조립 → LLM.
  최근 대화 6턴은 프롬프트에만 주입(검색엔 미사용). 답변에 근거 청크(sources) 동봉
- **유사사례** (`similar_case.py`): 요약문 벡터 검색 + 심의유형/KCD 배열 교집합 필터
- **검토서** (`review_doc.py`): 팩트시트(그래프 주문 체인 + 조문 원문 RAG + 분과 기준 + 유사사례)를
  결정적으로 만들고 → LLM은 문장화만. 산출 상태는 `HITL_REVIEW`
- **통계** (`stats.py`): LLM Text-to-SQL → **뷰 화이트리스트 검증**(`_is_safe`) 통과 시에만 실행
  → 결과를 LLM이 자연어 요약

### 7. 스캔 의무기록 OCR → 사건 변환 — `scripts/ocr_ingest_scans.py`, `services/scan_to_case.py`

신체검사 서류 스캔 PDF(보훈병원 영상검사결과지 등)를 의결서 파이프라인에 연결:

```
스캔 PDF → ①텍스트층/OCR(tesseract kor+eng, 200dpi) → ②정형 파싱 → scan_doc
        → ③사건 변환(application+disability+medical_record) → ④기존 의결서 흐름 그대로
```

- **② 파싱**: 이탤릭 제목이 OCR로 깨져도 견고하도록 **날짜 3종(의뢰/검사/판독일) 라인을
  블록 앵커**로 분할. 검사명·[Finding]·[Conclusion]·판독의 추출 + 폴백 휴리스틱
  (검사명=Finding 직전 라틴 라인, 판독의=사번 괄호 패턴). 원본 PDF는 `data/originals/scans/` 보존
- **③ 변환 — 서류 성격에 따라 두 갈래**:
  - `POST /scan-docs/{id}/to-case` (요건심사): 판독 소견 영문 패턴 → 상이처명 결정적 매핑
    (예: `ACL reconstruction` → 무릎 전방십자인대 파열(재건술 후), left → 좌).
    검사 블록마다 `medical_record` 1건. 신분·분과는 서류만으로 미상 → 기본값 + **담당자 확인 전제(HITL)**
  - `POST /scan-docs/{id}/to-grade` (상이등급): 신검 서류(신체검사 의사 소견서·검진결과통보서 등)는
    `grade_agenda` 안건으로 — 신검종류(재확인/재판정)·등급및분류번호·검진소견 추출 → 14컬럼 심사표 연결.
    실데이터는 `is_real` 표시(화면 파란 배지, mockgen 재시드에도 보존)
- 재적재·중복 변환 안전: 파일명 기준 대체, 등록번호(recv_no) 기준 기존 사건 재연결

### 8. LLM 게이트웨이 — `core/llm_client.py`

모든 생성 호출의 단일 관문. `core/prompts/*.txt` 템플릿 렌더 → 재시도(백오프) → 토큰 로깅.
- `FabrixClient`: OpenAI 호환 chat/completions (Ollama·FabriX 공통) — 연결 불가/모델 미설치는
  `LLMUnavailable`로 원인·해결 문구를 화면까지 전달
- `MockLLM`: 개발·테스트용 canned 응답 / `RuleCorrectLLM`: OCR 교정 규칙 대역

### 9. 에이전트 루프 (v0.2) — `core/reflexion.py`, `services/chatbot.py`

설계 원칙("사실 조립은 결정적, 서술만 LLM") 위에 **검증·검색 품질을 높이는 루프**만 추가:

- **챗봇 반복 검색** (`chatbot.answer`): 답변이 "확인되지 않습니다"면 `query_rewrite` 프롬프트로
  질의를 재작성(약칭→정식 명칭, 동의어 보강)해 재검색. **새 청크가 나온 경우에만** 재생성
  (최대 `CHAT_RETRY_MAX`회, 0=비활성). 응답에 `retried`·`rewritten_query` 동봉
- **문서 리플렉시온** (`core/reflexion.py:refine`): 검토서·의결서 초안을 사건 자료/팩트시트와
  대조 검증(`critique` 프롬프트: 환각·누락·결론 불일치·형식 위반) → 지적사항만 수정(`revise`)
  (최대 `REFLEXION_MAX_PASSES`회, 0=비활성). 지적사항은 `reflexion` 메타로 반환 —
  HITL 화면에서 담당자가 무엇이 걸러졌는지 확인. 검증 LLM 실패 시 초안 그대로 반환(관문 아님)
- **엔드투엔드 평가** (`scripts/eval_rag.py`): mockgen QA로 hit@5·미확인율·출처표기율·재시도율
  실측 (+`--judge` LLM 심판). `CHAT_RETRY_MAX=0`과 비교해 반복 검색 효과 측정

## DB 주요 테이블

| 테이블 | 내용 |
|---|---|
| `documents` / `chunks` | 적재 문서 + 청크(본문 tsvector, embedding vector(1024)) |
| `application` / `disability` / `medical_record` / `service_record` / `official_doc` | 사건 데이터 (신청→상이처→의무기록·병적·공적서류) |
| `conclusion` | 상이처별 판단(이원 결과·문안·상태), (app,dis,round) 유니크 |
| `kg_nodes` / `kg_edges` | 지식그래프 |
| `grade_criteria` / `grade_agenda` / `grade_case` / `grade_log` | 별표3 기준·등급심사 안건·과거 판정·작업로그 |
| `cases` | 유사사례 풀(요약 임베딩) / `feedback` 게시판 |
| `scan_doc` | 스캔 의무기록 OCR 적재(헤더·검사 블록 JSONB·원본 경로·연결 app_id) |

## 코드 맵

```
api/main.py      모든 라우트 (화면 서빙 + REST). 여기서 시작해 따라가면 됨
config/          전역 설정 — 임의 가정값은 전부 여기 격리, 환경변수로 재정의
core/            llm_client(게이트웨이) · retrieval(하이브리드 검색) · graph(그래프 탐색)
                 · subcommittee*(분과 프로필·판단모듈) · prompts/(LLM 템플릿)
services/        기능 로직 — chatbot · similar_case · review_doc · decision_doc
                 · grade_predict · grade_export(xlsx) · stats
ingestion/       ocr_adapter · verifier · chunker · embedder · indexer
db/              schema*.sql · seed/(CSV) · seed_codes · build_graph
mockgen/         시연 데이터 생성(정형화틀 기반, 멱등) — 표본 사건 33건
web/             index(메인) · board/feedback(피드백) · intake(OCR) + css/ + js/
tests/           test_smoke(무DB) · test_integration(DB 필요)
```

## 실행

```bash
bash scripts/setup_dev.sh    # 맥 원클릭 세팅 (상세: DEV_SETUP.md)
bash start.sh                # http://127.0.0.1:8000
# 모델·DB 없이 화면만: EMBED_BACKEND=hash LLM_BACKEND=mock ./.venv/bin/uvicorn api.main:app --port 8000
python3 tests/test_smoke.py  # 빠른 검증
```

환경 구축은 [DEV_SETUP.md](DEV_SETUP.md), 서버·벤치마크는 [SETUP.md](SETUP.md) 참조.
