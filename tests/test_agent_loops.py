"""DB·모델 없이 도는 v0.2 에이전트 루프 테스트: 챗봇 반복 검색 + 문서 리플렉시온.

실제 프롬프트 템플릿(core/prompts/*.txt) 렌더링을 그대로 거치므로
플레이스홀더 불일치도 함께 잡는다.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.graph
from core.llm_client import MockLLM
from core.reflexion import refine, _parse_issues
from services import chatbot
from services.review_doc import _strip_markdown


class FakeEmb:
    def encode(self, texts):
        return [[0.0] * 4 for _ in texts]


class SeqLLM(MockLLM):
    """prompt_name별 응답 큐 - 같은 프롬프트의 n번째 호출에 다른 답을 줄 수 있다."""

    def __init__(self, seq: dict[str, list[str]]):
        super().__init__()
        self.seq = {k: list(v) for k, v in seq.items()}
        self.calls = []

    def _call(self, prompt: str) -> str:
        self.calls.append(self._last_name)
        q = self.seq.get(self._last_name)
        if not q:
            raise AssertionError(f"예상 밖 LLM 호출: {self._last_name}")
        return q.pop(0)


def _hits(*ids):
    return [{"chunk_id": i, "content": f"내용{i}", "block_type": "PARA", "page_no": 1,
             "doc_id": 1, "source_path": f"/x/mock_{i:03d}.json#mock_{i:03d}", "score": 1.0}
            for i in ids]


def _no_graph(monkeypatch):
    monkeypatch.setattr(core.graph, "rule_facts", lambda q: [])


# ── 챗봇 반복 검색 ──────────────────────────────────────────────

def test_chat_law_priority_injection(monkeypatch):
    """조문 축약 질문은 법령 문서군 검색을 추가로 돌려 법령 청크를 최우선 주입."""
    _no_graph(monkeypatch)
    law_queries = []
    def fake_search(q, vec, top_k=5, doc_type=None, use_dense=True):
        if doc_type == "법령":
            law_queries.append(q)
            return _hits(9)
        return _hits(1, 2)
    monkeypatch.setattr(chatbot, "hybrid_search", fake_search)
    llm = SeqLLM({"chatbot": ["공상군경 요건입니다 [법령 p.1]"]})
    r = chatbot.answer("예우법 4-1-6 요건?", llm, FakeEmb())
    assert law_queries and "제4조제1항제6호" in law_queries[0], "법령 검색은 확장 질의로"
    assert [s["chunk_id"] for s in r["sources"]][0] == 9, "법령 청크 최우선"
    assert r["retried"] == 0 and llm.calls == ["chatbot"]
    print("PASS test_chat_law_priority_injection")


def test_chat_retry_clause_expansion_deterministic(monkeypatch):
    """법령 미적재 환경: 재작성은 LLM이 아니라 core/clauses.py가 결정적으로 처리."""
    _no_graph(monkeypatch)
    n_all = []
    def fake_search(q, vec, top_k=5, doc_type=None, use_dense=True):
        if doc_type == "법령":
            return []                       # 법령 미적재 환경
        n_all.append(q)
        return _hits(1, 2) if len(n_all) == 1 else _hits(3)
    monkeypatch.setattr(chatbot, "hybrid_search", fake_search)
    llm = SeqLLM({"chatbot": ["자료에서 확인되지 않습니다.",
                              "예우법 제4조제1항제6호에 따라 해당됩니다 [mock_003 p.1]"]})
    r = chatbot.answer("예우법 4-1-6 요건?", llm, FakeEmb())
    assert r["retried"] == 1
    assert "제4조제1항제6호" in r["rewritten_query"]
    assert "국가유공자 등 예우 및 지원에 관한 법률" in r["rewritten_query"]
    assert "해당됩니다" in r["answer"]
    ids = [s["chunk_id"] for s in r["sources"]]
    assert ids[0] == 3 and set(ids) == {1, 2, 3}, "새 근거 우선 + 기존 근거 병합"
    assert llm.calls == ["chatbot", "chatbot"], "법령 확장에 LLM 재작성을 쓰면 안 됨"
    print("PASS test_chat_retry_clause_expansion_deterministic")


def test_chat_retry_llm_fallback(monkeypatch):
    """조문 축약이 없는 질문은 LLM 재작성(동의어 보강)으로 폴백."""
    _no_graph(monkeypatch)
    n_search = []
    def fake_search(q, vec, top_k=5, doc_type=None, use_dense=True):
        n_search.append(q)
        return _hits(1) if len(n_search) == 1 else _hits(2)
    monkeypatch.setattr(chatbot, "hybrid_search", fake_search)
    llm = SeqLLM({"chatbot": ["자료에서 확인되지 않습니다.",
                              "추간판탈출증 기준은 다음과 같습니다 [mock_002 p.1]"],
                  "query_rewrite": ["추간판탈출증 판단기준 심사"]})
    r = chatbot.answer("허리 디스크 판단기준?", llm, FakeEmb())
    assert r["retried"] == 1 and r["rewritten_query"] == "추간판탈출증 판단기준 심사"
    assert llm.calls == ["chatbot", "query_rewrite", "chatbot"]
    print("PASS test_chat_retry_llm_fallback")


def test_chat_no_retry_when_answered(monkeypatch):
    _no_graph(monkeypatch)
    monkeypatch.setattr(chatbot, "hybrid_search",
                        lambda q, vec, top_k=5, doc_type=None, use_dense=True: _hits(1))
    llm = SeqLLM({"chatbot": ["제4조에 따라 해당됩니다 [mock_001 p.1]"]})
    r = chatbot.answer("예우법 4조?", llm, FakeEmb())
    assert r["retried"] == 0 and r["rewritten_query"] is None
    assert llm.calls == ["chatbot"], "근거를 찾았으면 재작성 호출이 없어야 함"
    print("PASS test_chat_no_retry_when_answered")


def test_chat_no_regen_without_new_evidence(monkeypatch):
    _no_graph(monkeypatch)
    monkeypatch.setattr(chatbot, "hybrid_search",
                        lambda q, vec, top_k=5, doc_type=None, use_dense=True: _hits(1, 2))
    llm = SeqLLM({"chatbot": ["자료에서 확인되지 않습니다."],
                  "query_rewrite": ["다른 검색어"]})
    r = chatbot.answer("근거 없는 질문", llm, FakeEmb())
    assert r["retried"] == 0, "재검색이 같은 청크만 돌려주면 재생성하지 않음"
    assert "확인되지 않습니다" in r["answer"]
    assert llm.calls == ["chatbot", "query_rewrite"]
    print("PASS test_chat_no_regen_without_new_evidence")


def test_chat_retry_disabled(monkeypatch):
    _no_graph(monkeypatch)
    monkeypatch.setattr(chatbot, "CHAT_RETRY_MAX", 0)
    monkeypatch.setattr(chatbot, "hybrid_search",
                        lambda q, vec, top_k=5, doc_type=None, use_dense=True: _hits(1))
    llm = SeqLLM({"chatbot": ["자료에서 확인되지 않습니다."]})
    r = chatbot.answer("질문", llm, FakeEmb())
    assert r["retried"] == 0 and llm.calls == ["chatbot"]
    print("PASS test_chat_retry_disabled")


def test_expand_clauses():
    from core.clauses import expand_clauses
    assert expand_clauses("보상법 2-1-3 적용 검토 사례") == \
        "보상법 제2조제1항제3호 보훈보상대상자 지원에 관한 법률 적용 검토 사례"
    assert expand_clauses("고엽제법 28-1 사례") == \
        "고엽제법 제28조제1항 고엽제후유의증 등 환자지원 및 단체설립에 관한 법률 사례"
    assert expand_clauses("예우법 4-1-6") == \
        "예우법 제4조제1항제6호 국가유공자 등 예우 및 지원에 관한 법률"
    assert expand_clauses("모르는법 1-2") == "모르는법 제1조제2항", "미등록 법명은 조문만 확장"
    assert expand_clauses("축약 없는 질문") == "축약 없는 질문"
    print("PASS test_expand_clauses")


# ── 리플렉시온 ──────────────────────────────────────────────────

def test_parse_issues():
    assert _parse_issues("문제없음") == []
    assert _parse_issues("문제 없음.") == []
    assert _parse_issues("") == []
    got = _parse_issues("- [환각] 없는 병원 인용\n- [결론] 결론 불일치")
    assert got == ["[환각] 없는 병원 인용", "[결론] 결론 불일치"]
    assert len(_parse_issues("\n".join(f"- 지적 {i}" for i in range(9)))) == 5, "상한 5건"
    print("PASS test_parse_issues")


def test_refine_clean_draft():
    llm = SeqLLM({"critique": ["문제없음"]})
    text, meta = refine("초안 본문", evidence="근거", verdict="해당", llm=llm, max_passes=1)
    assert text == "초안 본문"
    assert meta == {"critiques": 1, "issues": [], "revised": False}
    assert llm.calls == ["critique"], "지적이 없으면 수정 호출이 없어야 함"
    print("PASS test_refine_clean_draft")


def test_refine_revises_with_postprocess():
    llm = SeqLLM({"critique": ["- [환각] 근거에 없는 서울병원 인용"],
                  "revise": ["**수정된 본문이다.**"]})
    text, meta = refine("서울병원 소견에 따르면...", evidence="근거", verdict="비해당",
                        llm=llm, max_passes=1, postprocess=_strip_markdown)
    assert text == "수정된 본문이다.", "수정본에도 원 경로와 같은 후처리 적용"
    assert meta["revised"] and meta["issues"] == ["[환각] 근거에 없는 서울병원 인용"]
    assert llm.calls == ["critique", "revise"]
    print("PASS test_refine_revises_with_postprocess")


def test_refine_disabled():
    llm = SeqLLM({})
    text, meta = refine("초안", evidence="근거", verdict="해당", llm=llm, max_passes=0)
    assert text == "초안" and meta["critiques"] == 0 and llm.calls == []
    print("PASS test_refine_disabled")


def test_refine_survives_llm_failure():
    class BoomLLM(MockLLM):
        def _call(self, prompt):
            raise RuntimeError("LLM 다운")
    text, meta = refine("초안", evidence="근거", verdict="해당", llm=BoomLLM(), max_passes=1)
    assert text == "초안" and meta["revised"] is False, "검증 실패가 문서 생성을 막으면 안 됨"
    print("PASS test_refine_survives_llm_failure")


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))


# ── 정리본 (v0.2: txt -> 구조화 -> 열람) ──────────────────────

def test_clean_sections_and_text():
    from services.ocr_normalize import _build_sections, clean_text
    exams = [
        {"doc": "영상검사결과", "line": 12, "fields": {"disease": "요추 추간판탈출증", "date": "2024-03-02"},
         "norm": {"doc_type": "영상검사결과", "disease": "요추 추간판탈출증(L4-5)", "opinion": "진구성 소견",
                  "key_findings": ["L4-5 만성 퇴행성 변화"], "summary": "진구성", "source": "llm"}},
        {"doc": "소견서", "line": 80, "fields": {"disease": "당뇨병"}},   # 미정규화 - 파싱 필드 폴백
    ]
    secs = _build_sections(exams, "부산보훈병원")
    assert secs[0]["disease"] == "요추 추간판탈출증(L4-5)", "norm이 파싱 필드보다 우선"
    assert secs[0]["normalized"] and secs[0]["norm_source"] == "llm"
    assert secs[1]["disease"] == "당뇨병" and not secs[1]["normalized"], "미정규화는 파싱 필드 폴백"
    assert secs[1]["source_line"] == 80, "원문 라인 추적 보존"
    doc = {"sd_id": 7, "person": "홍길동", "doc_kind": "의무기록 묶음", "hospital": "부산보훈병원",
           "reg_no": "123", "file_name": "x.txt", "n_sections": 2, "n_normalized": 1, "sections": secs}
    txt = clean_text(doc)
    assert "1. 영상검사결과" in txt and "진구성 소견" in txt and "근거: L4-5" in txt
    assert "(원문 12행~)" in txt and "2. 소견서" in txt
    print("PASS test_clean_sections_and_text")


def test_clause_notes():
    from core.clauses import clause_notes
    notes = clause_notes("예우법 4-1-6 요건과 고엽제법 28-1 절차")
    assert notes[0] == "'예우법 4-1-6' = 예우법 제4조제1항제6호 (국가유공자 등 예우 및 지원에 관한 법률)"
    assert notes[1].startswith("'고엽제법 28-1' = 고엽제법 제28조제1항")
    assert clause_notes("축약 없는 질문") == []
    print("PASS test_clause_notes")
