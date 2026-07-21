"""기능② RAG 챗봇 = 하이브리드 검색 + LLM. 코어 조합만으로 완성 (코어 검증 겸용).

대화 기록은 세션 단위로 chat_session/chat_message에 저장 (DB명세서 v0.13 20·21 축약).
"""
import json
import psycopg
from psycopg.rows import dict_row
from config.settings import PG_DSN
from core.retrieval import hybrid_search
from core.llm_client import LLMClient


def answer(question: str, llm: LLMClient, emb, doc_type: str | None = None,
           history: list[dict] | None = None) -> dict:
    """emb: ingestion.embedder.get_embedder() 반환 객체 (encode 메서드).
    doc_type: 지정 시 해당 문서군만 검색 (예: UI 업로드분 'ui_upload').
    history: [{"role": "user"|"ai", "text": ...}] 최근 대화 - 프롬프트에만 주입(검색엔 미사용)."""
    vec = emb.encode([question])[0]
    hits = hybrid_search(question, vec, doc_type=doc_type)
    def _label(h):
        sp = str(h["source_path"])
        return sp.split("#", 1)[1] if "#" in sp else sp.replace("\\", "/").rsplit("/", 1)[-1]
    context = "\n---\n".join(
        f"[{_label(h)} p.{h['page_no']}] {h['content'][:800]}" for h in hits) or "(검색 결과 없음)"
    # 판단기준 그래프 근거 (질환→판단축→필요서류 멀티홉, 결정적) — v2.4 룰 그래프
    try:
        from core.graph import rule_facts
        facts = rule_facts(question)
        if facts:
            context = "[판단기준 그래프 — 정형화틀 v2.4]\n" + "\n".join(facts) + "\n---\n" + context
    except Exception:
        pass  # 그래프 미적재 환경에서도 챗봇은 동작
    hist = "\n".join(
        f"{'담당자' if m.get('role') == 'user' else 'AI'}: {str(m.get('text', ''))[:300]}"
        for m in (history or [])[-6:]) or "(없음)"
    text = llm.generate("chatbot", context=context, question=question, history=hist)
    return {"answer": text, "sources": hits}


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
