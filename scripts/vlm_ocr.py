# -*- coding: utf-8 -*-
"""VLM 기반 스캔 OCR 프로토타입 — OCR 솔루션 없이 비전 LLM(Gemma)으로 문자인식.

파이프라인: PDF → 페이지 이미지(200dpi PNG) → 비전 LLM 전사(창작 금지) → 페이지별 txt
검증용 베이스라인: --backend tesseract (동일 이미지로 tesseract kor+eng)
비교 리포트:      --compare (VLM vs tesseract 페이지별 유사도·길이)

실행 (맥북 Ollama):
  ollama pull gemma3:12b            # 비전 지원 — 파이프라인 검증용 (31B는 폐쇄망 GPU에서)
  python3 scripts/vlm_ocr.py <pdf...> [-o out_vlm] [--dpi 200] [--compare]
  VLM_MODEL=gemma3:12b VLM_ENDPOINT=http://localhost:11434/v1/chat/completions

폐쇄망(FabriX/vLLM 등 OpenAI 호환 비전 서빙):
  VLM_ENDPOINT=<서빙주소>/v1/chat/completions VLM_MODEL=gemma-4-31b python3 scripts/vlm_ocr.py ...

원칙: 전사 전용 — 원문에 없는 글자 생성 금지, 표는 '라벨: 값' 재구성, 판독불가는 ⟦판독불가⟧.
산출: out_vlm/<pdf명>/page_NN.txt + full.txt(\f 구분 — ocr_ingest_scans.py 입력 호환) + report.json
"""
import argparse
import base64
import difflib
import json
import os
import re
import sys
import time
from pathlib import Path

VLM_ENDPOINT = os.getenv("VLM_ENDPOINT", "http://localhost:11434/v1/chat/completions")
VLM_MODEL = os.getenv("VLM_MODEL", "gemma3:12b")
VLM_API_KEY = os.getenv("VLM_API_KEY", "")
TIMEOUT = int(os.getenv("VLM_TIMEOUT", "600"))

PROMPT = """너는 문서 전사(transcription) 담당자다. 첨부된 스캔 이미지의 글자를 그대로 옮겨 적어라.

규칙:
- 보이는 글자만 전사하라. 내용 창작·요약·번역·추측 금지.
- 읽을 수 없는 부분은 ⟦판독불가⟧ 로 표기하라. 그럴듯한 값으로 채우지 마라.
- 표는 각 행을 '라벨: 값' 형태의 줄로 재구성하라.
- 도장·서명·로고는 [서명], [직인], [로고] 로만 표기하라.
- 숫자·날짜·코드(예: M17.1, 7급5111호)는 한 글자도 바꾸지 마라. 애매하면 ⟦판독불가⟧.
- 설명이나 머리말 없이 전사 결과만 출력하라."""


def render_pages(pdf: Path, dpi: int):
    import fitz
    doc = fitz.open(pdf)
    for i, page in enumerate(doc, 1):
        pix = page.get_pixmap(dpi=dpi)
        yield i, pix.tobytes("png")


def split_tiles(png: bytes, n: int, overlap: float = 0.08) -> list[bytes]:
    """페이지를 세로 n등분(겹침 포함) — VLM 비전 인코더는 이미지를 ~900px대로 축소해서 보므로
    A4 전장을 통짜로 넣으면 글자가 뭉개진다. 타일로 나누면 타일당 유효 해상도가 n배."""
    if n <= 1:
        return [png]
    import io
    from PIL import Image
    im = Image.open(io.BytesIO(png))
    h = im.height
    band = h // n
    ov = int(band * overlap)
    out = []
    for k in range(n):
        top = max(0, k * band - ov)
        bot = min(h, (k + 1) * band + ov)
        buf = io.BytesIO()
        im.crop((0, top, im.width, bot)).save(buf, "PNG")
        out.append(buf.getvalue())
    return out


def selftest() -> int:
    """알려진 문구 이미지를 보내 이미지가 모델에 실제 전달되는지 확인.
    실패하면 Ollama 버전이 OpenAI 호환 이미지 입력을 무시하는 것 — ollama 업그레이드 필요."""
    from PIL import Image, ImageDraw, ImageFont
    import io
    probe = "보훈심사 검증 7124"
    im = Image.new("RGB", (640, 120), "white")
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", 48)
    except OSError:
        font = ImageFont.truetype("/System/Library/Fonts/AppleSDGothicNeo.ttc", 48)
    d.text((20, 30), probe, font=font, fill="black")
    buf = io.BytesIO(); im.save(buf, "PNG")
    out = vlm_transcribe(buf.getvalue())
    ok = "7124" in out and ("보훈" in out or "심사" in out)
    print(f"프로브 문구: {probe}\n모델 응답  : {out[:120]}")
    print("판정: " + ("정상 — 이미지가 모델에 전달됨" if ok
                     else "실패 — 이미지가 모델에 전달되지 않음. Ollama 업그레이드(brew upgrade ollama) 후 재시도"))
    return 0 if ok else 2


def vlm_transcribe(png: bytes) -> str:
    import requests
    b64 = base64.b64encode(png).decode()
    headers = {"Content-Type": "application/json"}
    if VLM_API_KEY:
        headers["Authorization"] = f"Bearer {VLM_API_KEY}"
    body = {
        "model": VLM_MODEL,
        "temperature": 0,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
    }
    r = requests.post(VLM_ENDPOINT, json=body, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def tess_transcribe(png: bytes) -> str:
    import io
    import pytesseract
    from PIL import Image
    return pytesseract.image_to_string(Image.open(io.BytesIO(png)), lang="kor+eng")


def mock_transcribe(png: bytes) -> str:
    return f"[MOCK 전사 — 이미지 {len(png)}바이트. 실행 환경에 비전 모델이 없어 파이프라인만 검증]"


def sanity(text: str) -> list[str]:
    """VLM 환각의 흔한 형태를 기계 검사 — 통과 못 하면 담당자 검수 필수 표시."""
    warns = []
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for l in set(lines):
        if len(l) > 3 and lines.count(l) >= 5:
            warns.append(f"반복 루프 의심: '{l[:30]}' ×{lines.count(l)}")
    if len(text) < 20:
        warns.append("전사량 과소 — 페이지 누락 의심")
    if re.search(r"(요약|정리하면|번역)\s*[:：]", text):
        warns.append("전사 외 생성(요약/번역) 의심")
    return warns


def main():
    ap = argparse.ArgumentParser(description="VLM 스캔 OCR 프로토타입")
    ap.add_argument("pdfs", nargs="*", help="스캔 PDF 경로")
    ap.add_argument("-o", "--out", default="out_vlm")
    ap.add_argument("--dpi", type=int, default=260)
    ap.add_argument("--tiles", type=int, default=3, help="페이지 세로 분할 수 (1=통짜 — 전장 A4는 글자 뭉개짐)")
    ap.add_argument("--backend", choices=["vlm", "tesseract", "mock"], default="vlm")
    ap.add_argument("--compare", action="store_true", help="VLM과 tesseract를 모두 돌려 페이지별 비교")
    ap.add_argument("--selftest", action="store_true", help="이미지가 모델에 전달되는지 프로브 문구로 확인")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.pdfs:
        ap.error("PDF 경로를 지정하거나 --selftest 를 사용하세요")

    fn = {"vlm": vlm_transcribe, "tesseract": tess_transcribe, "mock": mock_transcribe}
    for pdf_path in args.pdfs:
        pdf = Path(pdf_path)
        dest = Path(args.out) / pdf.stem
        dest.mkdir(parents=True, exist_ok=True)
        pages, report = [], {"file": pdf.name, "backend": args.backend, "model": VLM_MODEL, "pages": []}
        for no, png in render_pages(pdf, args.dpi):
            t0 = time.time()
            if args.backend == "vlm" and args.tiles > 1:
                text = "\n".join(fn["vlm"](t) for t in split_tiles(png, args.tiles))
            else:
                text = fn[args.backend](png)
            entry = {"page": no, "chars": len(text), "sec": round(time.time() - t0, 1),
                     "warnings": sanity(text) if args.backend == "vlm" else []}
            if args.compare and args.backend == "vlm":
                base = tess_transcribe(png)
                entry["tesseract_chars"] = len(base)
                entry["similarity"] = round(difflib.SequenceMatcher(None, text, base).ratio(), 3)
                (dest / f"page_{no:02d}_tesseract.txt").write_text(base, encoding="utf-8")
            (dest / f"page_{no:02d}.txt").write_text(text, encoding="utf-8")
            pages.append(text)
            report["pages"].append(entry)
            w = " ⚠ " + "; ".join(entry["warnings"]) if entry["warnings"] else ""
            print(f"  p{no}: {entry['chars']}자 {entry['sec']}s{w}")
        (dest / "full.txt").write_text("\f".join(pages), encoding="utf-8")
        (dest / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{pdf.name}: {len(pages)}쪽 → {dest}/ (full.txt는 ocr_ingest_scans.py 입력 호환)")


if __name__ == "__main__":
    sys.exit(main())
