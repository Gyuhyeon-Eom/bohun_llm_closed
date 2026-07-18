"""기능② RAG 챗봇 = 하이브리드 검색 + LLM. 코어 조합만으로 완성 (코어 검증 겸용)."""
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
    hist = "\n".join(
        f"{'담당자' if m.get('role') == 'user' else 'AI'}: {str(m.get('text', ''))[:300]}"
        for m in (history or [])[-6:]) or "(없음)"
    text = llm.generate("chatbot", context=context, question=question, history=hist)
    return {"answer": text, "sources": hits}
