# -*- coding: utf-8 -*-
"""플랫폼 API 계층 회귀 — 모델 라우팅·보안 스크럽/필터·API 임베더·재랭킹 (전부 오프라인 모의)."""
import pytest

import config.settings as st
from core.llm_client import FabrixClient, _scrub_rrn


class _Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code, self.text = data, status, str(data)

    def json(self):
        return self._d


def test_model_routing_table():
    """기능서 v0.1 배정표 — 경량/심층 프롬프트가 올바른 모델로 간다."""
    assert FabrixClient._model_for("judgment") == st.LLM_MODEL_MAIN
    assert FabrixClient._model_for("case_draft") == st.LLM_MODEL_MAIN
    for p in ("chatbot", "query_rewrite", "ocr_normalize", "similar_reason", "file_notes"):
        expect = st.LLM_MODEL_LIGHT if p in st.LLM_LIGHT_PROMPTS else st.LLM_MODEL_MAIN
        assert FabrixClient._model_for(p) == expect


def test_routing_reaches_request(monkeypatch):
    """실제 요청 페이로드의 model 필드까지 라우팅이 전달되는지."""
    sent = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        sent["model"] = json["model"]
        return _Resp({"choices": [{"message": {"content": "ok"}}], "usage": {}})

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(st, "LLM_MODEL_LIGHT", "gemma-4-31b", raising=False)
    FabrixClient()._call("질문", prompt_name="chatbot")
    assert sent["model"] == "gemma-4-31b"
    FabrixClient()._call("판단", prompt_name="judgment")
    assert sent["model"] == st.LLM_MODEL_MAIN


def test_rrn_scrub():
    assert _scrub_rrn("주민 440425-1110116") == "주민 440425-1******"
    assert _scrub_rrn("440425 1110116") == "440425-1******"
    assert _scrub_rrn("전화 010-1234-5678") == "전화 010-1234-5678"  # 오탐 없음


def test_security_gate_blocks(monkeypatch):
    """필터가 차단 판정하면 예외(사유 포함), 통과면 filtered_text 사용."""
    from core import llm_client as lc
    monkeypatch.setattr(st, "SECURITY_FILTER_API", "http://sec", raising=False)
    import core.provider_apis as pa
    monkeypatch.setattr(pa, "security_check",
                        lambda text, direction: (False, text, "개인정보 포함"))
    with pytest.raises(lc.LLMUnavailable, match="개인정보"):
        lc._security_gate("텍스트", "in")
    monkeypatch.setattr(pa, "security_check",
                        lambda text, direction: (True, "[치환] " + text, ""))
    assert lc._security_gate("텍스트", "in").startswith("[치환]")


def test_security_gate_fail_open_and_strict(monkeypatch):
    from core import llm_client as lc
    monkeypatch.setattr(st, "SECURITY_FILTER_API", "http://sec", raising=False)
    import core.provider_apis as pa

    def boom(text, direction):
        raise RuntimeError("연결 실패")

    monkeypatch.setattr(pa, "security_check", boom)
    assert lc._security_gate("텍스트", "in") == "텍스트"  # 기본 fail-open(스크럽은 별도 적용)
    monkeypatch.setattr(st, "SECURITY_FILTER_STRICT", True, raising=False)
    with pytest.raises(lc.LLMUnavailable):
        lc._security_gate("텍스트", "in")
    monkeypatch.setattr(st, "SECURITY_FILTER_STRICT", False, raising=False)


def test_api_embedder(monkeypatch):
    monkeypatch.setattr(st, "EMBEDDING_API", "http://emb", raising=False)
    import core.provider_apis as pa
    monkeypatch.setattr(pa, "EMBEDDING_API", "http://emb", raising=False)
    monkeypatch.setattr(pa, "_post", lambda url, payload, files=None, what="": {
        "data": [{"index": i, "embedding": [0.1] * st.EMBED_DIM}
                 for i in range(len(payload["input"]))]})
    from ingestion.embedder import ApiEmbedder
    out = ApiEmbedder().encode(["가", "나", "다"])
    assert len(out) == 3 and len(out[0]) == st.EMBED_DIM


def test_api_embedder_dim_mismatch(monkeypatch):
    import core.provider_apis as pa
    monkeypatch.setattr(pa, "EMBEDDING_API", "http://emb", raising=False)
    monkeypatch.setattr(pa, "_post", lambda url, payload, files=None, what="": {
        "data": [{"index": 0, "embedding": [0.1] * 768}]})
    from ingestion.embedder import ApiEmbedder
    with pytest.raises(RuntimeError, match="차원"):
        ApiEmbedder().encode(["가"])


def test_rerank_fallback(monkeypatch):
    """재랭커 장애 시 RRF 순서 유지 — 검색은 죽지 않는다."""
    from core.retrieval import _api_rerank
    hits = [{"content": f"c{i}"} for i in range(6)]
    monkeypatch.setattr(st, "RERANK_API", "http://rr", raising=False)
    import core.provider_apis as pa
    monkeypatch.setattr(pa, "rerank", lambda q, docs, k: [x for x in (3, 0, 5)][:k])
    out = _api_rerank("질의", hits, 3)
    assert [h["content"] for h in out] == ["c3", "c0", "c5"]

    def boom(q, docs, k):
        raise RuntimeError("장애")

    monkeypatch.setattr(pa, "rerank", boom)
    out = _api_rerank("질의", hits, 3)
    assert [h["content"] for h in out] == ["c0", "c1", "c2"]  # RRF 순서 유지
