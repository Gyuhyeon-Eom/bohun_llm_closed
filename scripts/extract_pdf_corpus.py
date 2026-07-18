"""PDF(텍스트 레이어) -> 페이지 번호를 보존한 코퍼스 JSON 추출.

목적: 출처 인용의 페이지 정확도. 기존 매뉴얼 추출본은 페이지가 전부 1로 고정되어
"매뉴얼 p.45" 클릭 시 스캔본 1쪽만 열렸다. 이 스크립트로 재추출하면 블록마다
실제 페이지가 남아, 출처 클릭 -> 원문 PDF의 그 페이지로 바로 점프한다.

사용법:
    python3 scripts/extract_pdf_corpus.py data/originals/manual_2.pdf --out data/manuals/manual_2.json
    python3 scripts/extract_pdf_corpus.py <스캔.pdf>          # --out 생략 시 <같은 이름>.json

이후 적재(예: python3 scripts/ingest_manuals.py)를 다시 실행하면 반영된다.

※ 텍스트 레이어가 없는 순수 이미지 스캔은 추출 불가 — 해당 페이지 목록을 경고로
  출력한다 (외부 OCR 산출 JSON을 같은 형식으로 만들어 넣는 것이 그 경우의 경로).
"""
import argparse
import json
import re
import sys
from pathlib import Path

from pypdf import PdfReader


def blocks_from_pdf(pdf: Path, min_chars: int) -> tuple[list[dict], list[int]]:
    reader = PdfReader(str(pdf))
    blocks, empty_pages = [], []
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if not text:
            empty_pages.append(i)
            continue
        # 페이지 내 문단 분리: 빈 줄 우선, 없으면 줄 단위 병합 후 문장 덩어리
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        if len(paras) <= 1:
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            paras, cur = [], ""
            for ln in lines:
                cur = f"{cur} {ln}".strip()
                if len(cur) >= 400:          # 지나치게 긴 병합 방지
                    paras.append(cur); cur = ""
            if cur:
                paras.append(cur)
        for p in paras:
            p = re.sub(r"\s+", " ", p)
            if len(p) >= min_chars:
                blocks.append({"type": "paragraph", "text": p, "page": i, "confidence": 1.0})
    return blocks, empty_pages


def main():
    ap = argparse.ArgumentParser(description="PDF -> 페이지 보존 코퍼스 JSON")
    ap.add_argument("pdf", help="텍스트 레이어가 있는 PDF (스캔+OCR 포함)")
    ap.add_argument("--out", help="출력 JSON 경로 (기본: PDF와 같은 이름.json)")
    ap.add_argument("--min-chars", type=int, default=15, help="이보다 짧은 조각은 버림")
    a = ap.parse_args()

    pdf = Path(a.pdf)
    if not pdf.is_file():
        sys.exit(f"✖ 파일 없음: {pdf}")
    out = Path(a.out) if a.out else pdf.with_suffix(".json")

    blocks, empty_pages = blocks_from_pdf(pdf, a.min_chars)
    if not blocks:
        sys.exit("✖ 추출된 텍스트가 없습니다 — 텍스트 레이어 없는 순수 이미지 스캔이면 외부 OCR 필요")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"doc_id": out.stem, "blocks": blocks}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    pages = sorted({b["page"] for b in blocks})
    print(f"추출 완료: {out}")
    print(f"  블록 {len(blocks)}개 / 페이지 {pages[0]}~{pages[-1]} (페이지 정보 보존)")
    if empty_pages:
        print(f"  ⚠ 텍스트 없는 페이지 {len(empty_pages)}쪽: {empty_pages[:20]}"
              f"{' …' if len(empty_pages) > 20 else ''} — 순수 이미지 스캔이면 외부 OCR 필요")


if __name__ == "__main__":
    main()
