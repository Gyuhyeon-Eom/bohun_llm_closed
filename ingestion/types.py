"""블록/청크 스키마. 기존 vlm_ocr 설계 계승 - OCR 엔진이 바뀌어도 이 스키마는 불변."""
from dataclasses import dataclass, field
from enum import Enum


class BlockType(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    FIGURE = "figure"
    LIST = "list"
    OTHER = "other"


@dataclass
class Block:
    type: BlockType
    text: str
    page_no: int
    meta: dict = field(default_factory=dict)


@dataclass
class Chunk:
    content: str
    block_type: BlockType
    page_no: int
    meta: dict = field(default_factory=dict)
