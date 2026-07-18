"""DB·모델 없이 도는 스모크 테스트: 청킹 규칙 + 통계 SQL 가드."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingestion.ocr_adapter import MockOCR
from ingestion.chunker import chunk_blocks
from ingestion.types import BlockType
from services.stats import _is_safe
from ingestion.verifier import verify_blocks
from ingestion.types import Block
from core.llm_client import MockLLM


def test_chunking():
    chunks = chunk_blocks(MockOCR().extract("dummy.pdf"))
    assert chunks, "청크가 비어있음"
    tables = [c for c in chunks if c.block_type == BlockType.TABLE]
    assert len(tables) == 1, "표는 통째로 1청크여야 함"
    assert all(len(c.content) <= 1200 + 1 for c in chunks)
    print("PASS test_chunking:", len(chunks), "chunks")


def test_sql_guard():
    assert _is_safe("SELECT * FROM v_stats_by_review_type")
    assert not _is_safe("DELETE FROM cases")
    assert not _is_safe("SELECT * FROM cases")  # 화이트리스트 밖
    print("PASS test_sql_guard")


def test_verifier():
    blocks = [
        Block(BlockType.PARAGRAPH, "저신뢰 텍스트", 1, {"confidence": 0.5}),
        Block(BlockType.PARAGRAPH, "고신뢰 텍스트", 1, {"confidence": 0.99}),
    ]
    out = verify_blocks(blocks, MockLLM())
    assert out[0].meta.get("verified") and out[0].meta["ocr_raw"] == "저신뢰 텍스트"
    assert not out[1].meta.get("verified"), "고신뢰 블록은 LLM 미투입이어야 함"
    print("PASS test_verifier")


if __name__ == "__main__":
    test_chunking()
    test_sql_guard()
    test_verifier()
