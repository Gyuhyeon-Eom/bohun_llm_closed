# -*- coding: utf-8 -*-
"""LangGraph 오케스트레이션·스토리지 계층 회귀 — plain 경로와 결과 동등성이 핵심."""
import importlib.util

import pytest

from core.llm_client import MockLLM
from ingestion.embedder import get_embedder

HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None


def _emb():
    import os
    os.environ["EMBED_BACKEND"] = "hash"
    return get_embedder()


def test_plain_answer_shape():
    from services import chatbot
    out = chatbot.answer("전방십자인대 파열 유사사례 있어?", MockLLM(), _emb())
    assert set(out) >= {"answer", "sources", "retried", "rewritten_query"}


@pytest.mark.skipif(not HAS_LANGGRAPH, reason="langgraph 미반입 환경 (폐쇄망 초기) — plain 폴백 경로가 대신 검증됨")
def test_graph_matches_plain():
    """같은 질문·MockLLM·hash 임베더 → plain과 langgraph 결과 동형."""
    from orchestration.chat_graph import run_chat_graph
    from services import chatbot
    q = "고엽제 후유증 판단기준 알려줘"
    plain = chatbot.answer(q, MockLLM(), _emb())
    graph = run_chat_graph(q, MockLLM(), _emb())
    assert graph["orchestrator"] == "langgraph"
    assert set(graph) >= set(plain)
    # MockLLM은 결정적 — 답변·근거 수·재시도 횟수가 일치해야 한다
    assert graph["retried"] == plain["retried"]
    assert len(graph["sources"]) == len(plain["sources"])


def test_fallback_when_backend_langgraph(monkeypatch):
    """ORCH_BACKEND=langgraph여도 응답 형태는 동일 (미반입이면 plain 폴백)."""
    import config.settings as st
    from services import chatbot
    monkeypatch.setattr(st, "ORCH_BACKEND", "langgraph", raising=False)
    out = chatbot.answer("안녕", MockLLM(), _emb())
    assert "answer" in out


def test_local_storage_roundtrip(tmp_path):
    from core import storage as stmod
    st = stmod.LocalStorage()
    st.base = tmp_path
    meta = st.put_bytes("scans/1/테스트.txt", "가나다".encode())
    assert meta["backend"] == "local" and st.exists(meta["obj_key"])
    assert st.get_bytes(meta["obj_key"]).decode() == "가나다"
    assert st.presigned_url(meta["obj_key"]) is None  # 로컬은 앱 경로 서빙


def test_get_storage_singleton_local():
    from core.storage import get_storage
    assert get_storage().backend in ("local", "minio")


def test_file_page_ddl():
    import psycopg
    from config.settings import PG_DSN
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns"
                    " WHERE table_name='file_page'")
        cols = {r[0] for r in cur.fetchall()}
    assert {"sd_id", "page_no", "ocr_done", "reviewed", "applied"} <= cols
