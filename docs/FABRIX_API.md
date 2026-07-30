# FabriX OpenAPI 연동 — 실규격 매핑 (260730)

근거: `docs/vendor/FabriX OpenAPI 매뉴얼.pdf` (삼성SDS, 2025). 요금표에서 계약한 API가
코드의 어느 지점에 어떤 실규격으로 연결되는지의 단일 참조 문서.

## 빠른 시작 — 이것만 설정하면 동작

```bash
FABRIX_BASE_URL=https://<발급 URL>      # OpenAPI 신청 시 발급 (…/openapi/* 의 뿌리)
FABRIX_CLIENT_KEY=eyJ...               # x-fabrix-client
FABRIX_PASS_KEY=eyJ...                 # x-openapi-token (Bearer 자동 부여)
FABRIX_MODEL_ID_MAIN=<uuid>            # gpt-oss-120b  ← GET /openapi/llm/v1/models
FABRIX_MODEL_ID_LIGHT=<uuid>           # gemma-4-31b
```

이 5개로 **LLM 생성·Security Filter·Parsing**이 실규격 경로로 자동 연결된다
(`SECURITY_FILTER_API`·`PARSING_API`를 비워두면 BASE_URL에서 유도).
점검: `python -m pipeline check`

## 인증 (전 API 공통 — 매뉴얼 서문)

| 헤더 | 값 | 필수 |
|---|---|---|
| `x-fabrix-client` | 클라이언트 키 | O |
| `x-openapi-token` | `Bearer <패스키>` | O |
| `x-generative-ai-user-email` | 포털 사용자 이메일 | 선택 |

구현: `core/provider_apis.py::fabrix_headers()` — `FABRIX_CLIENT_KEY` 미설정 시
기존 `Authorization: Bearer`(개발용 Ollama/vLLM)로 폴백. **키는 로그·매니페스트에 기록하지 않는다.**

## API 매핑표

| 요금표 항목 | FabriX 실규격 | 코드 지점 | 상태 |
|---|---|---|---|
| 모델 API (Gemma 4 31B·GPT-OSS 120B) | §5 LLM Serving `POST …/chat/completions` — OpenAI 호환 messages, 모델은 `x-llm-model-id` 헤더(UUID), body `model:"/mnt/models"` | `core/llm_client.py::FabrixClient` | ✅ 적용 |
| Security Filter | §3 `POST /openapi/filter/v1/check` — `{content, user_ip, target_model:"INTERNAL", target_service:"GENAI"}` → `data.is_blocked`/`result_code`(FR-400 차단) | `core/provider_apis.py::security_check` → `_security_gate`(LLM 입출력 훅) | ✅ 적용 |
| Parsing (a/b) | §11 비동기 3단계: `POST …/parsing-jobs/files`(multipart, useOcr/useTsr/useLmm) → `GET …/{jobId}` 폴링 → `GET …/{jobId}/result`(블록별 type/page/content) | `core/provider_apis.py::parse_document` (`ingest --transcriber parsing`) | ✅ 적용 |
| 이미지 분석 (VLM OCR) | §1 `POST /openapi/chat/v1/messages-with-models` — multipart, `modelIds:[TEXT, I2T]`, **파일 1개/호출**(타일 방식과 부합) | `scripts/vlm_ocr.py::fabrix_transcribe` (`--backend fabrix`, `ingest --transcriber fabrix`) | ✅ 적용 |
| Retrieval | §7 Knowledge `POST …/assets/{asset_id}/search` — FabriX에 등록한 지식 자산 내 검색 | `core/provider_apis.py::knowledge_search` (자산 등록 시 `KNOWLEDGE_ASSET_ID`) | ✅ 훅 (자산 미등록) |
| Chunking | §12 — **스토리지 사전등록(sourceObjUrl+VerifyKey) 기반, 인라인 텍스트 미지원** | 로컬 청커가 정식 경로 (`ingestion/chunker`) | ⚠ 로컬 확정 |
| Embedding | **텍스트→벡터 API 없음** (§8 rag-chat 임베딩은 FabriX 내부 지식 저장용 파일 단위) | 반입 bge-m3 로컬이 정식 경로 (`ingestion/embedder`) | ⚠ 로컬 확정 |
| Reranking | **전용 API 없음** (§7 검색은 자산 내 한정 — 임의 후보 재정렬 불가) | RRF 순서 유지, cross-encoder 서빙 확보 시 `RERANK_API` | ⚠ 로컬 확정 |
| RAG Chat | §8 — FabriX 내부 지식 기반 대화. 우리 RAG는 pgvector 자체 구축이라 미사용 | — | 미사용 |

## 보안 유의사항

1. **LLM Serving은 FabriX 서비스 필터가 적용되지 않는다**(§5.1 명시) — 그래서 이 코드는
   모든 생성 호출의 입·출력을 Security Filter(§3)에 통과시키고(`SECURITY_FILTER_MODE=in,out`),
   전송 직전 주민번호를 스크럽한다(`_scrub_rrn`). 필터 장애 시 기본 fail-open(스크럽은 유지),
   `SECURITY_FILTER_STRICT=1`이면 fail-closed.
2. Chat API(§1)를 쓰면 서비스 필터가 이중 적용되지만 OpenAI messages 형식이 아니라
   (`contents` 문자열 배열) 전환 비용이 있어 Serving+Filter 조합을 표준으로 한다.
3. 필터 응답은 판정만 하고 치환문을 주지 않는다 — 차단(FR-400) 시 해당 호출은 사유와 함께 중단.
4. `user_ip`는 필터 이력 추적용 필수 필드 — 운영 서버 IP를 `SECURITY_FILTER_USER_IP`로 설정.

## 확인 필요 (운영 투입 전, 발급 후 1회)

- [ ] 발급 URL 구조: LLM serving이 `{BASE}/openapi/llm/v1/chat/completions`인지
      (다르면 `FABRIX_ENDPOINT`로 직접 지정 — 매뉴얼 §5는 "발급 URL + /chat/completions"만 확정)
- [ ] `response_format`(json_schema) 지원 여부 — 미지원이어도 400 폴백으로 동작엔 지장 없음
- [ ] modelId 4종 조회: `GET /openapi/llm/v1/models`(생성 2종), `GET /openapi/chat/v1/all-models`(TEXT·I2T)
- [ ] Parsing `parserName` 값(기본 "FABRIX") 및 활용 사전협의(§11 — parsing-jobs 본문 방식은 사전협의 필요)
- [ ] 요청 한도: R40010(too many LLM request)·R40012(토큰 한도) 발생 시 재시도 정책 협의
