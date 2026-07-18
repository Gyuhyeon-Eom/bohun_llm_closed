"""블록 -> 청크. 문서 종류가 아니라 블록 타입 기준의 단일 규칙."""
from config.settings import CHUNK_MAX_CHARS, CHUNK_OVERLAP_CHARS
from ingestion.types import Block, BlockType, Chunk


def chunk_blocks(blocks: list[Block]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for b in blocks:
        if b.type in (BlockType.TABLE, BlockType.FIGURE):
            # 표·도표는 쪼개면 의미가 깨지므로 통째로 한 청크
            chunks.append(Chunk(b.text, b.type, b.page_no, b.meta))
        else:
            chunks.extend(_sliding(b))
    return chunks


def _sliding(b: Block) -> list[Chunk]:
    text, out, start = b.text, [], 0
    while start < len(text):
        end = min(start + CHUNK_MAX_CHARS, len(text))
        out.append(Chunk(text[start:end], b.type, b.page_no, b.meta))
        if end == len(text):
            break
        start = end - CHUNK_OVERLAP_CHARS
    return out
