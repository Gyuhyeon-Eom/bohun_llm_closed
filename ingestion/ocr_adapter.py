"""외부 상용 OCR 솔루션 어댑터.

TODO(확인): 솔루션 미선정. ExternalOCR은 "REST API를 제공하는 솔루션"이라는
  가정으로 임의 작성한 템플릿 - 계약 후 실제 SDK/API 규격에 맞춰 이 파일만 수정.
  어떤 솔루션이든 extract()가 Block 리스트를 반환하면 이후 파이프라인은 무수정.
"""
import os
from typing import Protocol
from ingestion.types import Block, BlockType


class OCREngine(Protocol):
    def extract(self, file_path: str) -> list[Block]: ...


class ExternalOCR:
    """REST 방식 가정 템플릿."""

    def __init__(self,
                 endpoint: str = os.getenv("OCR_ENDPOINT", "http://ocr.example/api/extract"),  # TODO(확인)
                 api_key: str = os.getenv("OCR_API_KEY", "")):
        self.endpoint, self.api_key = endpoint, api_key

    def extract(self, file_path: str) -> list[Block]:
        import requests
        with open(file_path, "rb") as f:
            # TODO(확인): 요청 스키마(멀티파트 필드명·인증 헤더)는 솔루션 규격에 맞춰 수정
            resp = requests.post(self.endpoint, files={"file": f},
                                 headers={"Authorization": f"Bearer {self.api_key}"}, timeout=300)
        resp.raise_for_status()
        # TODO(확인): 응답 -> Block 변환. 아래는 {"blocks":[{"type","text","page"}]} 형태 가정
        return [Block(BlockType(b.get("type", "other")), b["text"], b.get("page", 1))
                for b in resp.json().get("blocks", [])]


class MockOCR:
    """솔루션 없이 개발·테스트용."""

    def extract(self, file_path: str) -> list[Block]:
        return [
            Block(BlockType.HEADING, "국가유공자 요건 심의 검토자료", 1),
            Block(BlockType.PARAGRAPH,
                  "신청인은 군 복무 중 훈련으로 족지 굴곡변형(M21.27) 상이를 입었다고 주장한다. "
                  "관련 의무기록과 병상일지를 검토한 결과는 다음과 같다. " * 8, 1),
            Block(BlockType.TABLE, "판단대상 | 판단내용 | 결과\n족지 굴곡변형 | 예우법 4-1-4 | 검토중", 2),
        ]


class JsonOCR:
    """외부 OCR 산출물 JSON({"blocks":[{type,text,page,confidence}]})을 읽는 어댑터.
    목데이터(mockgen/)와 실전 외부 솔루션 산출물이 같은 경로로 들어오게 하는 표준 입구."""

    def extract(self, file_path: str) -> list[Block]:
        import json
        doc = json.loads(open(file_path, encoding="utf-8").read())
        return [Block(BlockType(b.get("type", "other")), b["text"], b.get("page", 1),
                      {"confidence": b.get("confidence", 1.0)})
                for b in doc["blocks"]]
