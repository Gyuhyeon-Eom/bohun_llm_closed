"""기능② RAG 챗봇 = 하이브리드 검색 + LLM. 코어 조합만으로 완성 (코어 검증 겸용).

대화 기록은 세션 단위로 chat_session/chat_message에 저장 (DB명세서 v0.13 20·21 축약).
"""
import json
import psycopg
from psycopg.rows import dict_row
from config.settings import PG_DSN, CHAT_RETRY_MAX, TOP_K
from core.retrieval import hybrid_search
from core.llm_client import LLMClient

# 챗봇 프롬프트 규칙 2의 "근거 없음" 답변 표지 - 반복 검색(재질의) 트리거
_NO_EVIDENCE = "확인되지 않습니다"


def _label(h):
    sp = str(h["source_path"])
    return sp.split("#", 1)[1] if "#" in sp else sp.replace("\\", "/").rsplit("/", 1)[-1]


def _render_context(graph_ctx: str, hits: list[dict]) -> str:
    context = "\n---\n".join(
        f"[{_label(h)} p.{h['page_no']}] {h['content'][:800]}" for h in hits) or "(검색 결과 없음)"
    return graph_ctx + context if graph_ctx else context


def initial_search(question: str, emb, doc_type: str | None = None):
    """1차 검색 + 결정적 컨텍스트(조문 보강·판단기준 룰그래프) 조립.
    plain 루프와 LangGraph 노드가 공유하는 빌딩블록 — 동작은 기존과 동일."""
    hits = hybrid_search(question, emb.encode([question])[0], doc_type=doc_type)
    # 조문 질의('예우법 4-1-6' 류) 결정적 보강 (core/clauses.py):
    # ① 법령 문서군 검색을 추가로 돌려 법령 원문 청크를 최우선 주입 - 매뉴얼·사례
    #    청크가 토큰 일치 수로 법령 원문을 랭킹 밖으로 밀어내는 실측 문제 보완
    # ② '축약 = 원문 표기' 안내를 컨텍스트에 주입 - 7B LLM이 두 표기를 연결 못 함
    from core.clauses import clause_notes, expand_clauses, has_clause
    graph_ctx = ""
    if has_clause(question):
        if doc_type is None:
            lq = expand_clauses(question)
            law_hits = hybrid_search(lq, emb.encode([lq])[0], top_k=3, doc_type="법령")
            law_ids = {h["chunk_id"] for h in law_hits}
            hits = (law_hits + [h for h in hits if h["chunk_id"] not in law_ids])[:TOP_K * 2]
        graph_ctx = "[조문 표기 안내] " + " / ".join(clause_notes(question)) + "\n---\n"
    # 판단기준 그래프 근거 (질환→판단축→필요서류 멀티홉, 결정적) — v2.4 룰 그래프.
    # 원 질문 기준으로 1회만 조립해 재시도 답변에도 동일하게 주입한다.
    try:
        from core.graph import rule_facts
        facts = rule_facts(question)
        if facts:
            graph_ctx += "[판단기준 그래프 — 정형화틀 v2.4]\n" + "\n".join(facts) + "\n---\n"
    except Exception:
        pass  # 그래프 미적재 환경에서도 챗봇은 동작
    # 실데이터 지식그래프(graph_rag) 경로는 제거(260806) — 구축·유지 비용 대비 효과가 낮아
    # pgvector 하이브리드(위 hybrid_search: 벡터+tsvector RRF)로 일원화. 관계형 질의는
    # 유사사례 구조화 필터(KCD·심의유형·심사구분, services/similar_case.py)가 담당.
    return hits, graph_ctx


def fmt_history(history: list[dict] | None) -> str:
    return "\n".join(
        f"{'담당자' if m.get('role') == 'user' else 'AI'}: {str(m.get('text', ''))[:300]}"
        for m in (history or [])[-6:]) or "(없음)"


def generate_answer(llm: LLMClient, question: str, graph_ctx: str, hits: list, hist: str) -> str:
    return llm.generate("chatbot", context=_render_context(graph_ctx, hits),
                        question=question, history=hist)


def rewrite_query(llm: LLMClient, prev_query: str, hits: list, tried: list[str]) -> str | None:
    """재검색 질의 산출 — 1순위: 조문 축약의 결정적 확장(법령명 매핑은 LLM 환각 불허,
    core/clauses.py), 폴백: LLM 재작성(동의어 보강). 새 질의가 없으면 None."""
    from core.clauses import expand_clauses
    rq = expand_clauses(prev_query)
    if rq in tried:   # 축약 표기가 없거나 이미 시도 - LLM 재작성 폴백
        try:
            rq = llm.generate("query_rewrite", question=prev_query,
                              context=_render_context("", hits)).strip().splitlines()[0][:200]
        except Exception:
            return None  # 재작성 실패는 1차 답변으로 응대 (재시도는 부가 기능)
    return rq if rq and rq not in tried else None


def merge_hits(new_hits: list, hits: list) -> list | None:
    """재검색 결과 병합 — 새 근거가 없으면 None(재생성 불필요)."""
    seen = {h["chunk_id"] for h in hits}
    if not any(h["chunk_id"] not in seen for h in new_hits):
        return None
    return (new_hits + [h for h in hits if h["chunk_id"]
                        not in {x["chunk_id"] for x in new_hits}])[:TOP_K * 2]


def answer(question: str, llm: LLMClient, emb, doc_type: str | None = None,
           history: list[dict] | None = None) -> dict:
    """emb: ingestion.embedder.get_embedder() 반환 객체 (encode 메서드).
    doc_type: 지정 시 해당 문서군만 검색 (예: UI 업로드분 'ui_upload').
    history: [{"role": "user"|"ai", "text": ...}] 최근 대화 - 프롬프트에만 주입(검색엔 미사용).

    v0.2 반복 검색: 답변이 "확인되지 않습니다"면 질의를 재작성해 재검색하고,
    새 근거가 나온 경우에만 재생성한다 (최대 CHAT_RETRY_MAX회). 인사말 등
    근거가 필요 없는 응대는 이 표지를 포함하지 않으므로 재시도가 걸리지 않는다.

    ORCH_BACKEND=langgraph면 동일 로직을 LangGraph 그래프로 실행(orchestration/chat_graph.py)
    — langgraph 미반입 환경은 자동으로 이 절차식(plain) 경로로 폴백."""
    from config.settings import ORCH_BACKEND
    if ORCH_BACKEND == "langgraph":
        try:
            from orchestration.chat_graph import run_chat_graph
            return run_chat_graph(question, llm, emb, doc_type=doc_type, history=history)
        except ImportError:
            pass  # langgraph 미반입 — plain 폴백
    hits, graph_ctx = initial_search(question, emb, doc_type)
    hist = fmt_history(history)
    text = generate_answer(llm, question, graph_ctx, hits, hist)
    retried, queries = 0, [question]
    while retried < CHAT_RETRY_MAX and _NO_EVIDENCE in text:
        rq = rewrite_query(llm, queries[-1], hits, queries)
        if not rq:
            break
        queries.append(rq)
        merged = merge_hits(hybrid_search(rq, emb.encode([rq])[0], doc_type=doc_type), hits)
        if merged is None:
            break  # 재검색이 새 근거를 못 찾음 - 재생성해도 결과 동일
        hits = merged
        text = generate_answer(llm, question, graph_ctx, hits, hist)
        retried += 1
    return {"answer": text, "sources": hits,
            "retried": retried, "rewritten_query": queries[-1] if retried else None}


# ── 세션 기록 (질문·답변 1왕복 단위로 저장, 세션은 첫 질문 시 자동 생성) ──
def save_exchange(session_id: int | None, question: str, answer: str, sources: list) -> int:
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        if session_id is None:
            cur.execute("INSERT INTO chat_session(title) VALUES (%s) RETURNING cs_id",
                        (question[:60],))
            session_id = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(max(seq),0) FROM chat_message WHERE cs_id=%s", (session_id,))
        seq = cur.fetchone()[0]
        cur.execute("INSERT INTO chat_message(cs_id, seq, role, content) VALUES (%s,%s,'user',%s)",
                    (session_id, seq + 1, question))
        cur.execute("INSERT INTO chat_message(cs_id, seq, role, content, sources) VALUES (%s,%s,'ai',%s,%s)",
                    (session_id, seq + 2, answer,
                     json.dumps([{k: s.get(k) for k in ("doc_id", "source_path", "page_no", "content", "block_type")}
                                 for s in (sources or [])], ensure_ascii=False, default=str)))
        cur.execute("UPDATE chat_session SET last_at=now() WHERE cs_id=%s", (session_id,))
    return session_id


def list_sessions(limit: int = 30) -> list[dict]:
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("""SELECT s.cs_id, s.title,
                              to_char(s.last_at, 'MM-DD HH24:MI') AS last_at,
                              count(m.cm_id) FILTER (WHERE m.role='user')::int AS n_q
                       FROM chat_session s LEFT JOIN chat_message m USING (cs_id)
                       GROUP BY s.cs_id ORDER BY s.last_at DESC LIMIT %s""", (limit,))
        return cur.fetchall()


def get_messages(session_id: int) -> list[dict]:
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("""SELECT role, content, sources
                       FROM chat_message WHERE cs_id=%s ORDER BY seq""", (session_id,))
        return cur.fetchall()
