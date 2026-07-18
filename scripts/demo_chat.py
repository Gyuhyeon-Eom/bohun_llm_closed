"""프로토타입 챗봇 CLI. 목데이터 코퍼스 대상 질의 -> 하이브리드 검색 -> 답변+출처.

실행: EMBED_BACKEND=hash python3 scripts/demo_chat.py [--raw]
  기본은 교정본(mock_fixed) 검색. --raw면 오염 원본 검색 (품질 차이 체감용).
운영 전환: get_embedder("bge") + FabrixClient로 교체하면 그대로 실 챗봇.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from ingestion.embedder import get_embedder
from core.retrieval import hybrid_search
from core.llm_client import get_llm
from core.prompts import __name__ as _  # noqa

EMB = get_embedder("hash")
LLM = get_llm()
DOC_TYPE = "mock_raw" if "--raw" in sys.argv else "mock_fixed"

print(f"보훈심사 프로토타입 챗봇 (코퍼스: {DOC_TYPE}, 종료: 빈 입력)")
while True:
    q = input("\n질문> ").strip()
    if not q:
        break
    hits = hybrid_search(q, EMB.encode([q])[0], top_k=3, doc_type=DOC_TYPE)
    if not hits:
        print("  관련 문서를 찾지 못했습니다."); continue
    ctx = "\n---\n".join(f"[{h['source_path'].split('/')[-1]}] {h['content'][:300]}" for h in hits)
    print("  " + LLM.generate("chatbot", context=ctx, question=q, history="").replace("\n", "\n  "))
    for h in hits:
        print(f"  · 출처: {h['source_path'].split('/')[-1].split('#')[0]} p.{h['page_no']} (score {h['score']:.4f})")
