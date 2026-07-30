# -*- coding: utf-8 -*-
"""챗봇 RAG 흐름의 LangGraph 오케스트레이션 — services/chatbot.py 빌딩블록 재사용.

그래프:  retrieve → generate → (근거 부족?) → rewrite → retrieve → … → END
                         └─ (근거 충분 or 재시도 소진) → END

plain 루프(services/chatbot.answer)와 로직·프롬프트·재시도 상한(CHAT_RETRY_MAX)이
동일하다 — 오케스트레이션 프레임워크만 다르고 결과는 같아야 하며, 이는
tests/test_orchestration.py에서 회귀 검증한다.

폐쇄망 반입: langgraph + langchain-core wheel (pva_ai- offline_bundles 보충분).
미반입 환경에서는 services/chatbot.answer가 ImportError를 잡아 plain으로 폴백한다.
"""
from typing import TypedDict

from langgraph.graph import StateGraph, START, END

from config.settings import CHAT_RETRY_MAX
from core.retrieval import hybrid_search
from services.chatbot import (
    _NO_EVIDENCE, fmt_history, generate_answer, initial_search, merge_hits, rewrite_query,
)


class ChatState(TypedDict, total=False):
    question: str
    doc_type: str | None
    hist: str
    hits: list
    graph_ctx: str
    answer: str
    retried: int
    queries: list[str]
    pending_query: str | None   # rewrite가 산출한 재검색 질의
    no_new: bool                # 재검색이 새 근거를 못 찾음 — 1차 답변 유지 종료


def build_graph(llm, emb):
    """호출 시점의 llm/emb를 클로저로 캡처 — 전역 상태 없음 (요청 단위 안전)."""

    def retrieve(state: ChatState) -> ChatState:
        if not state.get("hits"):  # 1차 검색 + 결정적 컨텍스트
            hits, graph_ctx = initial_search(state["question"], emb, state.get("doc_type"))
            return {"hits": hits, "graph_ctx": graph_ctx}
        rq = state.get("pending_query")
        merged = merge_hits(hybrid_search(rq, emb.encode([rq])[0],
                                          doc_type=state.get("doc_type")), state["hits"])
        if merged is None:  # 새 근거 없음 — plain과 동일하게 재생성 없이 1차 답변 유지
            return {"pending_query": None, "no_new": True}
        return {"hits": merged, "pending_query": None,
                "retried": state.get("retried", 0) + 1}

    def generate(state: ChatState) -> ChatState:
        text = generate_answer(llm, state["question"], state.get("graph_ctx", ""),
                               state["hits"], state["hist"])
        return {"answer": text}

    def rewrite(state: ChatState) -> ChatState:
        queries = state.get("queries") or [state["question"]]
        rq = rewrite_query(llm, queries[-1], state["hits"], queries)
        return {"pending_query": rq, "queries": queries + ([rq] if rq else [])}

    def need_retry(state: ChatState) -> str:
        if (state.get("retried", 0) < CHAT_RETRY_MAX
                and _NO_EVIDENCE in state.get("answer", "")):
            return "rewrite"
        return END

    def rewrite_ok(state: ChatState) -> str:
        return "retrieve" if state.get("pending_query") else END

    def after_retrieve(state: ChatState) -> str:
        return END if state.get("no_new") else "generate"

    g = StateGraph(ChatState)
    g.add_node("retrieve", retrieve)
    g.add_node("generate", generate)
    g.add_node("rewrite", rewrite)
    g.add_edge(START, "retrieve")
    g.add_conditional_edges("retrieve", after_retrieve, {"generate": "generate", END: END})
    g.add_conditional_edges("generate", need_retry, {"rewrite": "rewrite", END: END})
    g.add_conditional_edges("rewrite", rewrite_ok, {"retrieve": "retrieve", END: END})
    return g.compile()


def run_chat_graph(question: str, llm, emb, doc_type: str | None = None,
                   history: list[dict] | None = None) -> dict:
    """plain answer()와 동일한 응답 형태 반환 — 호출부(api/main.py) 무수정."""
    graph = build_graph(llm, emb)
    # 재귀 한도: 재시도 1회당 노드 3개 경유 + 여유
    out = graph.invoke(
        {"question": question, "doc_type": doc_type, "hist": fmt_history(history),
         "retried": 0, "queries": [question]},
        config={"recursion_limit": 6 + CHAT_RETRY_MAX * 6},
    )
    retried = out.get("retried", 0)
    queries = out.get("queries") or [question]
    return {"answer": out.get("answer", ""), "sources": out.get("hits", []),
            "retried": retried,
            "rewritten_query": queries[-1] if retried and len(queries) > 1 else None,
            "orchestrator": "langgraph"}
