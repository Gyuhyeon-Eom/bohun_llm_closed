"""OCR 품질 -> 검색 품질 영향 실측.

같은 목데이터 코퍼스를 (A) 오염 원본 그대로 / (B) verifier 교정 후 두 벌로 적재하고,
동일 QA 세트(질문 -> 정답 문서)로 검색 적중률(hit@5)을 비교한다.
verifier는 저신뢰 블록만 교정 - 실전 플로우(OCR->검증->청킹) 그대로.
실행: EMBED_BACKEND=hash python3 scripts/eval_ocr.py
"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import psycopg
from config.settings import PG_DSN
from ingestion.ocr_adapter import JsonOCR
from ingestion.verifier import verify_blocks
from ingestion.chunker import chunk_blocks
from ingestion.embedder import get_embedder
from ingestion.indexer import index_document
from core.retrieval import hybrid_search
from core.llm_client import RuleCorrectLLM

CORPUS = Path(__file__).parent.parent / "mockgen" / "corpus"
EMB = get_embedder("hash")


def ingest(doc_type: str, corrected: bool):
    ocr, fixer = JsonOCR(), RuleCorrectLLM()
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("DELETE FROM documents WHERE doc_type=%s", (doc_type,))
    for f in sorted(CORPUS.glob("mock_*.json")):
        blocks = ocr.extract(str(f))
        if corrected:
            blocks = verify_blocks(blocks, fixer)
        chunks = chunk_blocks(blocks)
        vecs = EMB.encode([c.content for c in chunks])
        # sha256이 파일 기준이라 두 벌 적재를 위해 doc_type을 해시에 섞음
        index_document(f"{f}#{doc_type}", doc_type, chunks, vecs, "mock-json")


SEV = lambda doc: ["high", "mid", "low"][int(doc.split("_")[1]) % 3]  # 생성 규칙과 동일


def hit_at_5(doc_type: str, qa: list[dict]) -> dict:
    out = {"all": [0, 0], "high": [0, 0], "mid": [0, 0], "low": [0, 0]}
    for item in qa:
        res = hybrid_search(item["q"], EMB.encode([item["q"]])[0], top_k=5, doc_type=doc_type,
                            use_dense=False)  # 대역 임베더의 무작위 dense가 RRF를 오염 -> sparse만 측정
        hit = any(item["expect_doc"] in r["source_path"] for r in res)
        for key in ("all", SEV(item["expect_doc"])):
            out[key][0] += hit; out[key][1] += 1
    return out


def main():
    qa = json.loads((CORPUS / "qa_set.json").read_text())
    ingest("mock_raw", corrected=False)
    ingest("mock_fixed", corrected=True)
    print(f"{'':10s} {'전체':>8s} {'오염심함':>8s} {'중간':>8s} {'약함':>8s}")
    for dt, label in [("mock_raw", "오염 원본"), ("mock_fixed", "교정 후")]:
        r = hit_at_5(dt, qa)
        cells = [f"{r[k][0]}/{r[k][1]}" for k in ("all", "high", "mid", "low")]
        print(f"{label:10s} {cells[0]:>8s} {cells[1]:>8s} {cells[2]:>8s} {cells[3]:>8s}")


if __name__ == "__main__":
    main()
