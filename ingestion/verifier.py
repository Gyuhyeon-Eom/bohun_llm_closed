"""OCR 후 LLM 검증 단계: 저신뢰 블록만 FabriX로 교정 (선별 검증).

플로우상 위치: OCR -> [여기] -> 청킹.
Block.meta["confidence"] (OCR 엔진 제공, 없으면 1.0 취급) 기준으로 선별.
교정된 블록은 meta["verified"]=True, 원문은 meta["ocr_raw"]에 보존.
"""
from ingestion.types import Block, BlockType
from core.llm_client import LLMClient
from config.settings import VERIFY_CONF_THRESHOLD, VERIFY_ALL


def _needs_verify(b: Block) -> bool:
    if b.type in (BlockType.FIGURE,):   # 텍스트 아닌 블록은 제외
        return False
    if VERIFY_ALL:
        return True
    return float(b.meta.get("confidence", 1.0)) < VERIFY_CONF_THRESHOLD


def verify_blocks(blocks: list[Block], llm: LLMClient) -> list[Block]:
    out = []
    for b in blocks:
        if _needs_verify(b):
            corrected = llm.generate("ocr_verify", ocr_text=b.text)
            b.meta.update(ocr_raw=b.text, verified=True)
            b = Block(b.type, corrected, b.page_no, b.meta)
        out.append(b)
    return out
