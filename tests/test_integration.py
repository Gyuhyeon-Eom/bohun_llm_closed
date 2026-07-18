"""통합 테스트: 실제 PostgreSQL+pgvector 대상 (PG_DSN 필요).

모델 없이 검증하도록 HashEmbedder + MockLLM 사용 - 검색 '품질'이 아니라
스키마·적재·검색·규칙매칭·예측·통계의 '동작'을 검증한다.
실행: PG_DSN=... python3 tests/test_integration.py
"""
import sys, os, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg
from config.settings import PG_DSN
from ingestion.ocr_adapter import MockOCR
from ingestion.chunker import chunk_blocks
from ingestion.embedder import get_embedder
from ingestion.indexer import index_document
from core.retrieval import hybrid_search
from core.llm_client import MockLLM
from services import similar_case, review_doc, stats, chatbot

EMB = get_embedder("hash")
LLM = MockLLM(canned={"stats_sql": "SELECT review_type, decision, cnt FROM v_stats_by_review_type"})


def setup_db():
    schema = open(os.path.join(os.path.dirname(__file__), "..", "db", "schema.sql")).read()
    with psycopg.connect(PG_DSN) as conn:
        conn.execute(schema)
    import db.seed_codes as seeder
    seeder.main()


def test_ingest_and_search():
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(os.urandom(64)); path = f.name
    blocks = MockOCR().extract(path)
    chunks = chunk_blocks(blocks)
    vecs = EMB.encode([c.content for c in chunks])
    n = index_document(path, "심사자료", chunks, vecs, "mock")
    assert n > 0
    # doc_type 필터: 법령·매뉴얼 등 대형 코퍼스와 격리해 목문서만 검증
    hits = hybrid_search("예우법 4-1-4", EMB.encode(["예우법 4-1-4"])[0], doc_type="심사자료")
    assert hits, "하이브리드 검색 결과 없음"
    assert any("예우법" in h["content"] for h in hits), "sparse 매칭 실패"
    print(f"PASS ingest+search: {n} chunks, top hit p.{hits[0]['page_no']}")


def test_review_doc_graph():
    import db.build_graph as bg
    bg.main()
    from core.graph import applied_clauses, cases_by_kcd, rule_conflicts
    orders = applied_clauses("상이공무원심의", "")
    assert orders, "그래프 APPLIES 탐색 실패"
    doc = review_doc.draft("요건심의", "상이공무원심의", "", "신청인은 훈련 중 부상",
                           LLM, EMB, ["M21.27"])
    fs = doc["fact_sheet"]
    assert fs["orders"] and fs["clause_passages"], "팩트시트 미완성"
    assert doc["status"] == "HITL_REVIEW"
    print(f"PASS review_doc(graph): 주문 {len(fs['orders'])}건, 조문근거 {len(fs['clause_passages'])}종, "
          f"유사사례 {len(fs['similar_cases'])}건, 규칙충돌 {len(rule_conflicts())}건")


def test_cases_similar():
    rows = []
    for i in range(6):  # 더미 사례
        decision = "해당" if i % 2 else "비해당"
        kcds = ["M21.27"] if i % 2 else ["S05.9"]
        rows.append(("요건심의", "신규", kcds, decision,
                     datetime.date(2024, 1 + i % 12, 1), f"더미 사례 {i}", EMB.encode([f"사례 {i}"])[0]))
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE cases RESTART IDENTITY")
        cur.executemany(
            "INSERT INTO cases(review_type,exam_category,kcd_codes,decision,decided_at,summary,summary_embedding)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s)", rows)
    sim = similar_case.find_similar(EMB.encode(["사례 3"])[0], "요건심의", n=3)
    assert len(sim) == 3
    kcd_sim = similar_case.find_similar(EMB.encode(["사례 3"])[0], kcd_codes=["M21.27"], n=3)
    assert all("M21.27" in c["kcd_codes"] for c in kcd_sim), "KCD 필터 실패"
    print(f"PASS similar: 유사 {len(sim)}건, KCD필터 {len(kcd_sim)}건")


def test_stats():
    out = stats.ask("심의유형별 판정 현황", LLM)
    assert "rows" in out and out["rows"], f"통계 실패: {out}"
    assert not stats._is_safe("SELECT * FROM cases")
    assert not stats._is_safe("DELETE FROM cases")
    print(f"PASS stats: {len(out['rows'])}행, sql={out['sql'][:50]}")


def test_chatbot():
    out = chatbot.answer("예우법 4-1-4 요건은?", LLM, EMB)
    assert out["answer"] and out["sources"]
    print(f"PASS chatbot: 출처 {len(out['sources'])}건")


if __name__ == "__main__":
    setup_db()
    test_ingest_and_search()
    test_cases_similar()
    test_review_doc_graph()
    test_stats()
    test_chatbot()
    print("\n=== 통합 테스트 전체 통과 ===")
