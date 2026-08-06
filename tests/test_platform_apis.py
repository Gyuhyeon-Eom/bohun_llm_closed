# -*- coding: utf-8 -*-
"""플랫폼 API 계층 회귀 — 모델 라우팅·보안 스크럽/필터·API 임베더·재랭킹
+ FabriX OpenAPI 실규격(260730 매뉴얼): 인증 헤더·x-llm-model-id·필터 check·파싱 잡 플로우.
전부 오프라인 모의."""
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


# ── FabriX OpenAPI 실규격 (docs/vendor/FabriX OpenAPI 매뉴얼.pdf) ───────────

def _fabrix_mode(monkeypatch, **model_ids):
    """실규격 모드 활성화 — settings와 provider_apis 모듈 전역을 함께 패치."""
    import core.provider_apis as pa
    vals = {"FABRIX_CLIENT_KEY": "ck-테스트", "FABRIX_PASS_KEY": "pk-테스트",
            "FABRIX_USER_EMAIL": "", **model_ids}
    for k, v in vals.items():
        monkeypatch.setattr(st, k, v, raising=False)
        if hasattr(pa, k):
            monkeypatch.setattr(pa, k, v, raising=False)
    return pa


def test_fabrix_headers_bearer_prefix(monkeypatch):
    """실규격 인증: x-fabrix-client + x-openapi-token(Bearer 자동 부여). 미설정=기존 Bearer."""
    pa = _fabrix_mode(monkeypatch)
    h = pa.fabrix_headers()
    assert h["x-fabrix-client"] == "ck-테스트"
    assert h["x-openapi-token"] == "Bearer pk-테스트"       # 프리픽스 자동
    monkeypatch.setattr(pa, "FABRIX_PASS_KEY", "Bearer pk2")
    assert pa.fabrix_headers()["x-openapi-token"] == "Bearer pk2"  # 중복 부여 없음
    monkeypatch.setattr(pa, "FABRIX_CLIENT_KEY", "")
    monkeypatch.setattr(pa, "PLATFORM_API_KEY", "legacy")
    assert pa.fabrix_headers() == {"Authorization": "Bearer legacy"}


def test_fabrix_serving_model_id_header(monkeypatch):
    """LLM Serving(§5): x-llm-model-id로 모델 선택, body model은 /mnt/models 고정."""
    _fabrix_mode(monkeypatch, FABRIX_MODEL_ID_MAIN="uuid-main",
                 FABRIX_MODEL_ID_LIGHT="uuid-light")
    sent = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.update(headers=headers, body=json)
        return _Resp({"choices": [{"message": {"content": "ok"}}], "usage": {}})

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    FabrixClient()._call("질문", prompt_name="chatbot")     # 경량 프롬프트
    assert sent["headers"]["x-llm-model-id"] == "uuid-light"
    assert sent["headers"]["x-fabrix-client"] == "ck-테스트"
    assert sent["body"]["model"] == st.FABRIX_SERVING_MODEL  # "/mnt/models"
    FabrixClient()._call("판단", prompt_name="judgment")     # 심층 프롬프트
    assert sent["headers"]["x-llm-model-id"] == "uuid-main"


def test_security_check_fabrix_schema(monkeypatch):
    """필터 check(§3): 요청 필수 필드 + data.is_blocked/FR-400 차단, FR-200 통과."""
    import core.provider_apis as pa
    monkeypatch.setattr(pa, "SECURITY_FILTER_API", "http://f/openapi/filter/v1/check")
    sent = {}

    def fake_post(url, payload, files=None, what=""):
        sent.update(payload)
        return {"statusCode": 200,
                "data": {"is_blocked": True, "result_code": "FR-400",
                         "reason_kor": "개인정보 포함", "message": "blocked"}}

    monkeypatch.setattr(pa, "_post", fake_post)
    allowed, text, reason = pa.security_check("주민번호가 든 문장", "in")
    assert allowed is False and reason == "개인정보 포함"
    for k in ("content", "user_ip", "target_model", "target_service"):
        assert k in sent                                     # §3 필수 필드
    assert sent["target_model"] == "INTERNAL"

    monkeypatch.setattr(pa, "_post", lambda u, p, files=None, what="": {
        "statusCode": 200, "data": {"is_blocked": False, "result_code": "FR-200"}})
    allowed, text, _ = pa.security_check("정상 문장", "out")
    assert allowed is True and text == "정상 문장"


def test_parse_document_job_flow(monkeypatch, tmp_path):
    """파싱(§11) 3단계: 업로드→폴링(EXECUTING→COMPLETED)→결과를 페이지 텍스트로 조립."""
    import core.provider_apis as pa
    monkeypatch.setattr(pa, "PARSING_API",
                        "http://f/openapi/parsing/v1/documents/parsing-jobs/files")
    monkeypatch.setattr(pa, "PARSING_POLL_S", 0)
    doc = tmp_path / "scan.pdf"
    doc.write_bytes(b"%PDF-fake")
    calls = {"status": 0}

    monkeypatch.setattr(pa, "_post", lambda url, payload, files=None, what="": {
        "parsingJobId": "job-1", "status": "QUEUED"})

    def fake_get(url, params=None, what=""):
        if url.endswith("/result"):
            assert "job-1" in url
            return [{"type": "text", "page": "2", "content": "둘째 쪽"},
                    {"type": "title", "page": "1", "subtitle": "판독지", "content": "첫째 쪽"},
                    {"type": "text", "page": "1", "content": "이어지는 내용"}]
        calls["status"] += 1
        return {"parsingJobId": "job-1",
                "status": "EXECUTING" if calls["status"] == 1 else "COMPLETED"}

    monkeypatch.setattr(pa, "_get", fake_get)
    pages = pa.parse_document(str(doc))
    assert len(pages) == 2 and calls["status"] == 2
    assert pages[0].startswith("판독지\n첫째 쪽") and "이어지는 내용" in pages[0]
    assert pages[1] == "둘째 쪽"


def test_parse_document_failed_job(monkeypatch, tmp_path):
    import core.provider_apis as pa
    monkeypatch.setattr(pa, "PARSING_API", "http://f/x/parsing-jobs/files")
    monkeypatch.setattr(pa, "PARSING_POLL_S", 0)
    doc = tmp_path / "s.pdf"
    doc.write_bytes(b"x")
    monkeypatch.setattr(pa, "_post", lambda *a, **k: {"parsingJobId": "j", "status": "QUEUED"})
    monkeypatch.setattr(pa, "_get", lambda *a, **k: {
        "status": "FAILED", "errorType": "OCR_ERROR", "message": {"ko": "손상 파일"}})
    with pytest.raises(RuntimeError, match="손상 파일"):
        pa.parse_document(str(doc))
