"""검색 성능 벤치마크 - 실환경에서 원커맨드로 실성능 측정.

측정 매트릭스: {dense, sparse, hybrid} x {오염 원본, verifier 교정} x 오염강도(high/mid/low)
지표: hit@1, hit@5 + 임베딩 처리량(청크/초, 160만 페이지 배치 소요 추정 근거)

실행 (실환경, bge-m3):   python3 scripts/benchmark.py
실행 (모델 없이 검증):    EMBED_BACKEND=hash python3 scripts/benchmark.py
  주의: hash 백엔드의 dense/hybrid 수치는 무의미한 자리표시자다 (의미 유사성 없음).
        sparse 열만 유효하며, 실수치는 bge-m3로 측정할 것.
산출: 콘솔 표 + scripts/benchmark_result.json
"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import psycopg
from config.settings import PG_DSN, EMBED_BACKEND
from ingestion.ocr_adapter import JsonOCR
from ingestion.verifier import verify_blocks
from ingestion.chunker import chunk_blocks
from ingestion.embedder import get_embedder
from ingestion.indexer import index_document
from core.retrieval import hybrid_search
from core.llm_client import RuleCorrectLLM

CORPUS = Path(__file__).parent.parent / "mockgen" / "corpus"
EMB = get_embedder()
SEV = lambda doc: ["high", "mid", "low"][int(doc.split("_")[1]) % 3]


def ingest(doc_type: str, corrected: bool) -> float:
    """적재 + 임베딩 처리량(청크/초) 반환."""
    ocr, fixer = JsonOCR(), RuleCorrectLLM()
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("DELETE FROM documents WHERE doc_type=%s", (doc_type,))
    n_chunks, t_embed = 0, 0.0
    for f in sorted(CORPUS.glob("mock_*.json")):
        blocks = ocr.extract(str(f))
        if corrected:
            blocks = verify_blocks(blocks, fixer)
        chunks = chunk_blocks(blocks)
        t0 = time.perf_counter()
        vecs = EMB.encode([c.content for c in chunks])
        t_embed += time.perf_counter() - t0
        n_chunks += len(chunks)
        index_document(f"{f}#{doc_type}", doc_type, chunks, vecs, "mock-json")
    return n_chunks / t_embed if t_embed else 0.0


def measure(doc_type: str, qa: list[dict], mode: str) -> dict:
    out = {k: {"h1": 0, "h5": 0, "n": 0} for k in ("all", "high", "mid", "low")}
    for item in qa:
        vec = EMB.encode([item["q"]])[0]
        if mode == "dense":
            res = hybrid_search("___none___", vec, top_k=5, doc_type=doc_type)  # sparse 무효화
        elif mode == "sparse":
            res = hybrid_search(item["q"], vec, top_k=5, doc_type=doc_type, use_dense=False)
        else:
            res = hybrid_search(item["q"], vec, top_k=5, doc_type=doc_type)
        ranks = [item["expect_doc"] in r["source_path"] for r in res]
        for key in ("all", SEV(item["expect_doc"])):
            out[key]["h1"] += bool(ranks and ranks[0])
            out[key]["h5"] += any(ranks)
            out[key]["n"] += 1
    return out


def main():
    qa = json.loads((CORPUS / "qa_set.json").read_text())
    print(f"임베딩 백엔드: {EMBED_BACKEND}"
          + ("  [경고: hash - dense/hybrid 수치 무의미, sparse만 유효]" if EMBED_BACKEND == "hash" else ""))
    tput = {"mock_raw": ingest("mock_raw", False), "mock_fixed": ingest("mock_fixed", True)}
    print(f"임베딩 처리량: {tput['mock_raw']:.0f} 청크/초"
          f"  (참고: 160만 페이지 x 페이지당 ~3청크 기준 배치 소요 추정에 사용)")
    result = {}
    print(f"\n{'구성':24s} {'hit@1':>7s} {'hit@5':>7s} | 강도별 hit@5 (심함/중간/약함)")
    for dt, label in (("mock_raw", "오염 원본"), ("mock_fixed", "교정 후")):
        for mode in ("dense", "sparse", "hybrid"):
            r = measure(dt, qa, mode)
            a = r["all"]
            row = f"{label}+{mode:6s}"
            sev = "/".join(f"{r[k]['h5']}/{r[k]['n']}" for k in ("high", "mid", "low"))
            print(f"{row:24s} {a['h1']}/{a['n']:>4d} {a['h5']}/{a['n']:>4d} | {sev}")
            result[f"{dt}.{mode}"] = r
    out = Path(__file__).parent / "benchmark_result.json"
    out.write_text(json.dumps({"backend": EMBED_BACKEND, "throughput": tput,
                               "results": result}, ensure_ascii=False, indent=1))
    print(f"\n결과 저장: {out}")


if __name__ == "__main__":
    main()
