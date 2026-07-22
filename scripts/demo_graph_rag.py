# -*- coding: utf-8 -*-
"""그래프 RAG 체감 데모 — 실데이터 OCR 적재분 기준.

사용:
  python3 scripts/demo_graph_rag.py                  # 내장 질의 세트 실행
  python3 scripts/demo_graph_rag.py "정기조 등급은?"  # 단일 질의
  python3 scripts/demo_graph_rag.py --chat "질문"     # 그래프+벡터+LLM 전체 파이프라인

전제 (순서대로):
  1. 실데이터 적재:   /ocr.html 업로드 또는 python3 scripts/ocr_ingest_scans.py --dir <txt폴더>
  2. 안건 변환:       python3 scripts/to_grade_all.py
  3. 그래프 재생성:   python3 db/build_graph.py && python3 scripts/build_rule_graph.py
                     && python3 db/build_instance_graph.py
LLM 미연결(mock) 환경에서도 그래프 사실 라인은 그대로 확인된다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_QUERIES = [
    "정기조 씨의 상이등급이 어떻게 되나요?",
    "허혈성심장질환 실데이터 대상자는 몇 명인가요?",
    "당뇨병 대상자 명단과 판정 기준 알려줘",
    "S26 코드는 어떤 질환인가요?",
    "파킨슨병 안건 현황 알려줘",
    "폐암으로 재판정 신청한 대상자가 있나요?",
]


def main():
    args = [a for a in sys.argv[1:]]
    chat = "--chat" in args
    if chat:
        args.remove("--chat")
    queries = args or DEFAULT_QUERIES

    from core.graph_rag import graph_facts
    llm = emb = None
    if chat:
        from core.llm_client import get_llm
        from ingestion.embedder import get_embedder
        from services.chatbot import answer
        llm, emb = get_llm(), get_embedder()

    for q in queries:
        print(f"\n{'=' * 60}\n■ {q}")
        r = graph_facts(q)
        ents = {k: v for k, v in r["entities"].items() if v}
        print(f"  인식 엔티티: {ents or '(없음)'}")
        for f in r["facts"]:
            print(f"  · {f}")
        if not r["facts"]:
            print("  (그래프 사실 없음 — 벡터 검색만으로 답변)")
        if chat:
            from services.chatbot import answer
            res = answer(q, llm, emb)
            print(f"  → 답변: {res['answer'][:400]}")
            print(f"  → 벡터 소스 {len(res['sources'])}건"
                  + (f", 재검색 {res['retried']}회" if res.get("retried") else ""))


if __name__ == "__main__":
    main()
