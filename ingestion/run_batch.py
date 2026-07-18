"""배치 진입점: 디렉토리의 PDF들을 OCR->LLM검증->청킹->임베딩->적재.
160만 페이지 배치든 신규 문서 증분이든 같은 코드."""
import sys, glob
from ingestion.ocr_adapter import MockOCR  # TODO: ExternalOCR로 교체
from ingestion.chunker import chunk_blocks
from ingestion.verifier import verify_blocks
from core.llm_client import MockLLM  # TODO: FabriX 규격 확정 후 LLMClient로 교체
from ingestion.embedder import get_embedder
from ingestion.indexer import index_document


def main(src_dir: str):
    ocr, emb, llm = MockOCR(), get_embedder(), MockLLM()
    for path in glob.glob(f"{src_dir}/**/*.pdf", recursive=True):
        blocks = ocr.extract(path)
        blocks = verify_blocks(blocks, llm)   # OCR -> LLM 검증 -> 청킹
        chunks = chunk_blocks(blocks)
        vecs = emb.encode([c.content for c in chunks])
        n = index_document(path, "unknown", chunks, vecs, ocr_engine="mock")
        print(f"{path}: {n} chunks")


if __name__ == "__main__":
    main(sys.argv[1])
