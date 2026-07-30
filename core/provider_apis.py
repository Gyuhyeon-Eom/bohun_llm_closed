# -*- coding: utf-8 -*-
"""FabriX OpenAPI 어댑터 — Parsing·Security Filter·Knowledge 검색 (+로컬 폴백 규약).

260730 실규격 반영: docs/vendor/FabriX OpenAPI 매뉴얼.pdf 기준.
  - 인증(전 API 공통): x-fabrix-client(클라이언트 키) + x-openapi-token(Bearer 패스키)
    + x-generative-ai-user-email(선택). FABRIX_CLIENT_KEY 미설정 시 기존
    Authorization Bearer(개발용 Ollama/vLLM) 유지.
  - Security Filter(§3): POST /openapi/filter/v1/check — data.is_blocked/result_code(FR-4xx=차단)
  - Parsing(§11): multipart 업로드 → 잡 폴링(QUEUED→COMPLETED) → 결과(블록별 type/page/content)
  - Knowledge(§7): 자산 내 chunk 검색 — 자산 등록(KNOWLEDGE_ASSET_ID) 시만
  - Chunking(§12)은 스토리지 사전등록(sourceObjUrl+VerifyKey) 기반이라 인라인 텍스트 미지원,
    Embedding은 텍스트→벡터 API 자체가 없음(rag-chat 임베딩은 FabriX 내부 지식 저장용)
    → 청킹·임베딩은 로컬(bge-m3)이 정식 경로. 아래 어댑터는 별도 서빙 확보 시의 훅.

공통 규약:
  - 로그·매니페스트에 키·본문 미기록
  - 재시도: 5xx·타임아웃 2회 지수백오프 / 4xx 즉시 전파
  - 엔드포인트 미설정: ProviderUnavailable — 호출부가 로컬 폴백/생략을 결정
"""
import time

from config.settings import (
    API_TIMEOUT_S, CHUNKING_API, EMBEDDING_API, FABRIX_BASE_URL, FABRIX_CLIENT_KEY,
    FABRIX_PASS_KEY, FABRIX_USER_EMAIL, KNOWLEDGE_ASSET_ID, PARSING_API,
    PARSING_POLL_S, PARSING_TIMEOUT_S, PLATFORM_API_KEY, RERANK_API,
    SECURITY_FILTER_API, SECURITY_FILTER_USER_IP,
)


class ProviderUnavailable(RuntimeError):
    """엔드포인트 미설정·연결 불가 — 호출부는 로컬 폴백 또는 명확한 안내로 처리."""


def fabrix_headers(extra: dict | None = None) -> dict:
    """FabriX 실규격 인증 헤더(§공통). CLIENT_KEY 미설정 시 기존 Bearer(개발 서빙)."""
    if FABRIX_CLIENT_KEY:
        token = FABRIX_PASS_KEY if FABRIX_PASS_KEY.startswith("Bearer ") \
            else f"Bearer {FABRIX_PASS_KEY}"
        h = {"x-fabrix-client": FABRIX_CLIENT_KEY, "x-openapi-token": token}
        if FABRIX_USER_EMAIL:
            h["x-generative-ai-user-email"] = FABRIX_USER_EMAIL
    else:
        h = {"Authorization": f"Bearer {PLATFORM_API_KEY}"} if PLATFORM_API_KEY else {}
    if extra:
        h.update(extra)
    return h


def _request(method: str, url: str, payload=None, files=None, what: str = "API") -> dict:
    import requests
    if not url:
        raise ProviderUnavailable(f"{what} 엔드포인트 미설정")
    last = None
    for attempt in range(3):
        try:
            r = requests.request(method, url, headers=fabrix_headers(),
                                 json=None if (files or method == "GET") else payload,
                                 data=payload if files else None, files=files,
                                 params=payload if method == "GET" else None,
                                 timeout=API_TIMEOUT_S)
            if r.status_code >= 500:
                last = RuntimeError(f"{what} 서버 오류({r.status_code})")
            elif r.status_code >= 400:
                raise RuntimeError(f"{what} 요청 오류({r.status_code}): {r.text[:200]}")
            else:
                return r.json()
        except requests.exceptions.ConnectionError as e:
            raise ProviderUnavailable(f"{what} 연결 불가({url})") from e
        except requests.exceptions.Timeout as e:
            last = e
        time.sleep(2 ** attempt)
    raise RuntimeError(f"{what} 3회 실패: {last}")


def _post(url: str, payload: dict, files=None, what: str = "API") -> dict:
    return _request("POST", url, payload, files, what)


def _get(url: str, params: dict | None = None, what: str = "API") -> dict:
    return _request("GET", url, params, None, what)


# ── Parsing API(§11): 문서 업로드 → 잡 폴링 → 페이지 텍스트 ─────────────────
def parse_document(path: str, use_ocr: bool = True, use_tsr: bool = True,
                   use_lmm: bool = False) -> list[str]:
    """반환: 페이지별 텍스트 리스트(1-base 페이지순).

    실규격 3단계 — ①POST …/parsing-jobs/files(multipart) → parsingJobId
    ②GET …/parsing-jobs/{id} 폴링(QUEUED/EXECUTING→COMPLETED/FAILED)
    ③GET …/parsing-jobs/{id}/result → [{type,anchor,page,subtitle,content}] 페이지별 조립.
    useOcr/useTsr(표 구조)/useLmm은 문자열 "True"/"False"로 전송(§11 규격)."""
    name = path.rsplit("/", 1)[-1]
    form = {"parserName": "FABRIX", "useOcr": str(use_ocr), "useTsr": str(use_tsr),
            "useLmm": str(use_lmm)}
    with open(path, "rb") as f:
        job = _post(PARSING_API, form, files={"file": (name, f)}, what="Parsing API")
    job_id = job.get("parsingJobId")
    if not job_id:
        # 동기 응답 서빙(개발용) 호환 — 잡 없이 바로 결과를 준 경우
        return _pages_from_blocks(job) or [job.get("text", "")]
    base = PARSING_API.rsplit("/files", 1)[0]           # …/documents/parsing-jobs
    deadline = time.time() + PARSING_TIMEOUT_S
    while True:
        st = _get(f"{base}/{job_id}", what="Parsing 상태")
        status = (st.get("status") or "").upper()
        if status == "COMPLETED":
            break
        if status == "FAILED":
            msg = (st.get("message") or {})
            raise RuntimeError(f"Parsing 실패({st.get('errorType')}): "
                               f"{msg.get('ko') or msg.get('en') or ''}")
        if time.time() > deadline:
            raise RuntimeError(f"Parsing 시간 초과({PARSING_TIMEOUT_S}s) — job {job_id}")
        time.sleep(PARSING_POLL_S)
    res = _get(f"{base}/{job_id}/result", what="Parsing 결과")
    return _pages_from_blocks(res) or [""]


def _pages_from_blocks(res) -> list[str]:
    """결과 블록([{type,page,subtitle,content}]) → 페이지별 텍스트. 형태 변형 허용."""
    blocks = res if isinstance(res, list) else (
        res.get("data") or res.get("blocks") or res.get("results") or []) \
        if isinstance(res, dict) else []
    pages: dict[int, list[str]] = {}
    for b in blocks:
        if not isinstance(b, dict):
            continue
        try:
            pg = int(str(b.get("page") or "1").strip() or 1)
        except ValueError:
            pg = 1
        content = (b.get("content") or "").strip()
        if content:
            sub = (b.get("subtitle") or "").strip()
            pages.setdefault(pg, []).append(f"{sub}\n{content}" if sub else content)
    return ["\n\n".join(pages[k]) for k in sorted(pages)] if pages else []


# ── Chunking API(§12) — 스토리지 사전등록 기반: 인라인 텍스트 미지원 ─────────
def chunk_text(text: str, max_chars: int, overlap: int) -> list[str]:
    """FabriX Chunking은 sourceObjUrl+VerifyKey(사전등록 스토리지) 입력만 받는다 —
    파이프라인의 인라인 청킹은 로컬 청커가 정식 경로. 이 훅은 인라인 지원
    서빙을 별도 확보한 경우만 사용(CHUNKING_API 직접 지정)."""
    data = _post(CHUNKING_API, {"text": text, "max_chars": max_chars, "overlap": overlap},
                 what="Chunking API")
    return data.get("chunks", [])


# ── Embedding — FabriX엔 텍스트→벡터 API 없음: bge-m3 로컬 확정 ─────────────
def embed_texts(texts: list[str]) -> list[list[float]]:
    """FabriX rag-chat 임베딩은 FabriX 내부 지식 저장용(파일 단위)이라 우리 pgvector에
    쓸 수 없다 — 임베딩은 반입 bge-m3 로컬이 정식 경로. 이 훅은 OpenAI 호환
    임베딩 서빙을 별도 확보한 경우만 사용(EMBEDDING_API 직접 지정)."""
    data = _post(EMBEDDING_API, {"input": texts}, what="Embedding API")
    if "data" in data:   # OpenAI 호환
        return [d["embedding"] for d in sorted(data["data"], key=lambda d: d.get("index", 0))]
    return data.get("embeddings", [])


# ── Reranking — FabriX엔 전용 rerank API 없음 ──────────────────────────────
def rerank(query: str, candidates: list[str], top_k: int) -> list[int]:
    """반환: 상위 top_k 후보의 원본 인덱스(관련도순). FabriX Knowledge 검색(§7)은
    자산 내 검색이라 임의 후보 재정렬에 못 쓴다 — 이 훅은 cross-encoder 서빙을
    별도 확보한 경우만 사용(RERANK_API 직접 지정). 미설정 시 RRF 순서 유지."""
    data = _post(RERANK_API, {"query": query, "documents": candidates, "top_k": top_k},
                 what="Reranking API")
    if "results" in data:   # {results:[{index, relevance_score}]} (Cohere류)
        return [r["index"] for r in sorted(data["results"],
                                           key=lambda r: -r.get("relevance_score", 0))][:top_k]
    return list(data.get("indices", []))[:top_k]


# ── Knowledge API(§7): 자산 내 chunk 검색 — 자산 등록 시 별도 근거 소스 ──────
def knowledge_search(query: str, asset_id: str = "", filters: list | None = None) -> list[dict]:
    """반환: [{content, title, filename, rankScore, ...}] — FabriX에 지식 자산을
    등록한 경우(심사 매뉴얼 등) 로컬 pgvector RAG와 병행할 추가 근거 소스."""
    aid = asset_id or KNOWLEDGE_ASSET_ID
    if not (FABRIX_BASE_URL and aid):
        raise ProviderUnavailable("Knowledge 검색 미설정(FABRIX_BASE_URL·KNOWLEDGE_ASSET_ID)")
    url = f"{FABRIX_BASE_URL}/openapi/knowledge/v1/knowledge-retrieval/assets/{aid}/search"
    data = _post(url, {"query": query, "filters": filters or [],
                       "additionalInformation": {}}, what="Knowledge API")
    return data.get("results", [])


# ── Security Filter API(§3): 질의·생성문 필터 판정 ──────────────────────────
def security_check(text: str, direction: str) -> tuple[bool, str, str]:
    """반환: (허용 여부, 텍스트, 사유). direction: 'in'|'out'.

    실규격 — POST /openapi/filter/v1/check
      요청: {content, user_ip, target_model:"INTERNAL", target_service:"GENAI", model_name}
      응답: {statusCode, data:{is_blocked, result_code(FR-200 통과/FR-400 차단),
             reason_kor, message, ...}} — 필터는 판정만 하고 치환문은 주지 않는다."""
    body = {"content": text, "user_ip": SECURITY_FILTER_USER_IP,
            "target_model": "INTERNAL", "target_service": "GENAI",
            "model_name": f"bohun-ai:{direction}"}
    data = _post(SECURITY_FILTER_API, body, what="Security Filter API")
    d = data.get("data") if isinstance(data.get("data"), dict) else data
    if "is_blocked" in d:                       # FabriX 실규격
        blocked = bool(d.get("is_blocked"))
        reason = d.get("reason_kor") or d.get("message") or d.get("result_code") or ""
        return (not blocked, text, reason)
    # 구 가정 스키마(개발 모의 서버) 호환
    return (bool(d.get("allowed", True)), d.get("filtered_text") or text,
            d.get("reason", ""))
