# -*- coding: utf-8 -*-
"""플랫폼 제공 API 어댑터 — Parsing·Chunking·Embedding·Reranking·Security Filter.

260730 요금표 기준: 생성 모델(Chat API)뿐 아니라 전처리·검색·보안까지 전부 API 호출.
각 API의 실규격(URL 경로·페이로드)은 벤더 문서 확정 시 이 파일만 수정하면
전 기능에 반영된다 (FabriX 게이트웨이와 같은 격리 원칙 — TODO(규격) 표기).

공통 규약:
  - 인증: Bearer PLATFORM_API_KEY (로그·매니페스트에 키·본문 미기록)
  - 재시도: 5xx·타임아웃 2회 지수백오프 / 4xx 즉시 전파
  - 엔드포인트 미설정: ProviderUnavailable — 호출부가 로컬 폴백/생략을 결정
"""
import time

from config.settings import (
    API_TIMEOUT_S, CHUNKING_API, EMBEDDING_API, PARSING_API, PLATFORM_API_KEY,
    RERANK_API, SECURITY_FILTER_API,
)


class ProviderUnavailable(RuntimeError):
    """엔드포인트 미설정·연결 불가 — 호출부는 로컬 폴백 또는 명확한 안내로 처리."""


def _post(url: str, payload: dict, files=None, what: str = "API") -> dict:
    import requests
    if not url:
        raise ProviderUnavailable(f"{what} 엔드포인트 미설정")
    headers = {"Authorization": f"Bearer {PLATFORM_API_KEY}"} if PLATFORM_API_KEY else {}
    last = None
    for attempt in range(3):
        try:
            r = requests.post(url, headers=headers,
                              json=None if files else payload,
                              data=payload if files else None, files=files,
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


# ── Parsing API: 문서(PDF 등) → 페이지 텍스트 ──────────────────────────────
def parse_document(path: str) -> list[str]:
    """반환: 페이지별 텍스트 리스트. TODO(규격): 멀티파트 필드명·응답 스키마 확정 시 조정."""
    with open(path, "rb") as f:
        data = _post(PARSING_API, {}, files={"file": (path.rsplit("/", 1)[-1], f)},
                     what="Parsing API")
    # 가정 스키마: {"pages": [{"page": 1, "text": "..."}]} 또는 {"text": "..."}
    if "pages" in data:
        return [p.get("text", "") for p in sorted(data["pages"], key=lambda p: p.get("page", 0))]
    return [data.get("text", "")]


# ── Chunking API (미설정 시 호출부가 로컬 청커 사용) ─────────────────────────
def chunk_text(text: str, max_chars: int, overlap: int) -> list[str]:
    data = _post(CHUNKING_API, {"text": text, "max_chars": max_chars, "overlap": overlap},
                 what="Chunking API")
    return data.get("chunks", [])


# ── Embedding API ──────────────────────────────────────────────────────────
def embed_texts(texts: list[str]) -> list[list[float]]:
    """TODO(규격): OpenAI 호환 {input:[...]}→{data:[{embedding}]} 가정."""
    data = _post(EMBEDDING_API, {"input": texts}, what="Embedding API")
    if "data" in data:   # OpenAI 호환
        return [d["embedding"] for d in sorted(data["data"], key=lambda d: d.get("index", 0))]
    return data.get("embeddings", [])


# ── Reranking API: 질의-후보 재정렬 ─────────────────────────────────────────
def rerank(query: str, candidates: list[str], top_k: int) -> list[int]:
    """반환: 상위 top_k 후보의 원본 인덱스(관련도순). TODO(규격): 응답 스키마 확정 시 조정."""
    data = _post(RERANK_API, {"query": query, "documents": candidates, "top_k": top_k},
                 what="Reranking API")
    if "results" in data:   # {results:[{index, relevance_score}]} (Cohere류)
        return [r["index"] for r in sorted(data["results"],
                                           key=lambda r: -r.get("relevance_score", 0))][:top_k]
    return list(data.get("indices", []))[:top_k]


# ── Security Filter API: 입출력 검사 ───────────────────────────────────────
def security_check(text: str, direction: str) -> tuple[bool, str, str]:
    """반환: (허용 여부, 통과/치환 텍스트, 사유). direction: 'in'|'out'.
    TODO(규격): {text, direction}→{allowed, filtered_text, reason} 가정."""
    data = _post(SECURITY_FILTER_API, {"text": text, "direction": direction},
                 what="Security Filter API")
    return (bool(data.get("allowed", True)),
            data.get("filtered_text") or text,
            data.get("reason", ""))
