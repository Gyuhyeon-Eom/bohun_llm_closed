"""T/F 제공 보훈심사실무 지침(텍스트 파일) -> documents 적재 (doc_type='실무지침').

심사1과·2과·3과 자료처럼 확장자와 무관하게 UTF-8 텍스트인 파일을 문단 단위로
청킹해 RAG 코퍼스에 넣는다. 챗봇·AI검토·레퍼런스가 근거로 검색하게 된다.
실행: python3 scripts/ingest_tf_manuals.py <파일1> [파일2 ...]
예:   python3 scripts/ingest_tf_manuals.py ~/docs/1__250416_심사1과*.pdf ~/docs/2__*.pdf
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import psycopg

from config.settings import PG_DSN
from ingestion.chunker import chunk_blocks
from ingestion.embedder import get_embedder
from ingestion.indexer import index_document, original_for
from ingestion.types import Block, BlockType


def text_to_blocks(path: Path) -> list[Block]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    # 빈 줄 기준 문단 분리, 짧은 장식 줄 제거
    paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", raw)]
    paras = [p for p in paras if len(p) >= 20]
    return [Block(type=BlockType.PARAGRAPH, text=p, page_no=i // 8 + 1)
            for i, p in enumerate(paras)]


def main():
    files = [Path(a) for a in sys.argv[1:]]
    if not files:
        print(__doc__)
        return
    emb = get_embedder()
    dt = "실무지침"
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("DELETE FROM documents WHERE doc_type=%s", (dt,))
    for f in files:
        if not f.exists():
            print(f"  건너뜀(없음): {f}")
            continue
        chunks = chunk_blocks(text_to_blocks(f))
        vecs = emb.encode([c.content for c in chunks])
        orig = original_for(f.stem)   # data/originals/<같은 이름>.pdf (스캔본)
        n = index_document(f"{f}#{dt}", dt, chunks, vecs, "tf-text", orig_path=orig)
        print(f"  {f.name}: {n} 청크" + (" (스캔 원본 연결됨)" if orig else ""))
    print("실무지침 적재 완료 - 챗봇·AI검토에서 근거로 검색됩니다")


if __name__ == "__main__":
    main()
