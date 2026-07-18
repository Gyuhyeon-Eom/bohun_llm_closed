"""분과 매뉴얼 블록(data/manuals/manual_N.json) -> chunks 적재 (doc_type='매뉴얼:N권').

의결서 생성 시 안건의 분과에 해당하는 매뉴얼만 우선 검색된다 (분과별 가중).
실행: python3 scripts/ingest_manuals.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import psycopg
from config.settings import PG_DSN
from ingestion.ocr_adapter import JsonOCR
from ingestion.chunker import chunk_blocks
from ingestion.embedder import get_embedder
from ingestion.indexer import index_document

SRC = Path(__file__).parent.parent / "data" / "manuals"


def main():
    emb, ocr = get_embedder(), JsonOCR()
    for f in sorted(SRC.glob("manual_*.json")):
        no = f.stem.split("_")[1]
        dt = f"매뉴얼:{no}권"
        with psycopg.connect(PG_DSN) as conn:
            conn.execute("DELETE FROM documents WHERE doc_type=%s", (dt,))
        chunks = chunk_blocks(ocr.extract(str(f)))
        vecs = emb.encode([c.content for c in chunks])
        n = index_document(f"{f}#{dt}", dt, chunks, vecs, "manual-text")
        print(f"  {dt}: {n} 청크")


if __name__ == "__main__":
    main()
