"""법령 조문(data/laws/*.json) -> chunks 적재 (doc_type='법령').

현재는 핵심 조문(예우법 4조, 보상법 2조)의 정제 텍스트.
외부 OCR로 법령 전문 디지털화 후에는 그 산출 JSON을 같은 경로로 적재하면 된다.
실행: python3 scripts/ingest_laws.py
스캔 원본 연결: data/originals/<json과 같은 이름>.pdf 를 두고 실행.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import psycopg
from config.settings import PG_DSN
from ingestion.ocr_adapter import JsonOCR
from ingestion.chunker import chunk_blocks
from ingestion.embedder import get_embedder
from ingestion.indexer import index_document, original_for

LAWS = Path(__file__).parent.parent / "data" / "laws"


def main():
    emb, ocr = get_embedder(), JsonOCR()
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("DELETE FROM documents WHERE doc_type='법령'")
    for f in sorted(LAWS.glob("*.json")):
        if f.name.startswith("grade_criteria"):   # 별표3 데이터는 별도 스크립트 담당
            continue
        chunks = chunk_blocks(ocr.extract(str(f)))
        vecs = emb.encode([c.content for c in chunks])
        orig = original_for(f.stem)   # data/originals/<이름>.pdf (관보 스캔 등)
        n = index_document(f"{f}#법령", "법령", chunks, vecs, "curated", orig_path=orig)
        print(f"  {f.name}: {n} 청크" + (" (스캔 원본 연결됨)" if orig else ""))


if __name__ == "__main__":
    main()
