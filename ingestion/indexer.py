"""청크+벡터 -> PostgreSQL 적재. 문서 sha256 기준 멱등."""
import hashlib
from pathlib import Path
import psycopg
from config.settings import PG_DSN
from ingestion.types import Chunk

# 스캔 원본(PDF) 보관소: 코퍼스 JSON과 같은 이름(stem)의 PDF를 여기 두면
# 적재 시 orig_path로 연결되어, 출처 클릭 시 스캔본의 해당 페이지가 열린다.
# (내부 자료이므로 git에 넣지 않는다 — .gitignore 처리)
ORIGINALS_DIR = Path(__file__).parent.parent / "data" / "originals"


def original_for(stem: str, display: str | None = None) -> str | None:
    """data/originals/<stem>.pdf 존재 시 orig_path 문자열('경로#표시명') 반환."""
    p = ORIGINALS_DIR / f"{stem}.pdf"
    return f"{p}#{display or p.name}" if p.is_file() else None


def sha256_of(path: str) -> str:
    """'경로#태그' 형태를 허용 - 같은 파일을 다른 doc_type으로 병행 적재할 때 사용."""
    real, _, tag = path.partition("#")
    h = hashlib.sha256(tag.encode())
    with open(real, "rb") as f:
        for part in iter(lambda: f.read(1 << 20), b""):
            h.update(part)
    return h.hexdigest()


def index_document(path: str, doc_type: str, chunks: list[Chunk],
                   vectors: list[list[float]], ocr_engine: str,
                   orig_path: str | None = None) -> int:
    """orig_path: 원본 스캔 파일(PDF 등) 경로 — OCR 텍스트(path)와 별개로 보관 (원문 열람용)."""
    assert len(chunks) == len(vectors)
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        digest = sha256_of(path)
        cur.execute("SELECT doc_id FROM documents WHERE sha256=%s", (digest,))
        if cur.fetchone():
            return 0  # 이미 적재됨 (멱등)
        cur.execute(
            "INSERT INTO documents(source_path, doc_type, sha256, ocr_engine, orig_path)"
            " VALUES (%s,%s,%s,%s,%s) RETURNING doc_id",
            (path, doc_type, digest, ocr_engine, orig_path))
        doc_id = cur.fetchone()[0]
        for c, v in zip(chunks, vectors):
            cur.execute(
                "INSERT INTO chunks(doc_id, block_type, content, page_no, embedding)"
                " VALUES (%s,%s,%s,%s,%s)",
                (doc_id, c.block_type.value, c.content.replace("\x00", ""), c.page_no, v))
        return len(chunks)
