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

원칙: 전사 전용 — 원문에 없는 글자 생성 금지, 표는 「항목명: 기재값」 병기, 판독불가는 ⟦판독불가⟧.
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

# FabriX I2T(이미지 분석 — 매뉴얼 §1 messages-with-models): 파일 1개/호출 제한이라
# 타일 1장씩 보내는 우리 방식과 부합. --backend fabrix 로 사용.
FABRIX_BASE_URL = os.getenv("FABRIX_BASE_URL", "").rstrip("/")
FABRIX_CLIENT_KEY = os.getenv("FABRIX_CLIENT_KEY", "")
FABRIX_PASS_KEY = os.getenv("FABRIX_PASS_KEY", "")
FABRIX_USER_EMAIL = os.getenv("FABRIX_USER_EMAIL", "")
FABRIX_TEXT_MODEL_ID = os.getenv("FABRIX_TEXT_MODEL_ID", "")
FABRIX_I2T_MODEL_ID = os.getenv("FABRIX_I2T_MODEL_ID", "")

PROMPT = """너는 문서 전사(transcription) 담당자다. 첨부된 스캔 이미지의 글자를 그대로 옮겨 적어라.

규칙:
- 보이는 글자만, 읽는 순서대로 전사하라. 내용 창작·요약·번역·추측 금지.
- 페이지 최상단의 기관명·문서 제목부터 빠뜨리지 말고 전사하라 (로고 옆 글자 포함).
- 표는 한 행씩, 그 행의 항목명과 기재값을 같은 줄에 「항목명: 기재값」으로 붙여 써라.
  (예: 성명: 홍길동) '라벨', '값' 같은 단어를 새로 만들어 붙이지 마라.
- 기재값이 비어 있는 항목·빈 칸·빈 행은 아예 출력하지 마라.
  빈 칸은 ⟦판독불가⟧가 아니다 — 글자가 없으면 그 항목 자체를 건너뛰어라.
- 같은 줄이나 같은 구절을 두 번 이상 반복해 쓰지 마라. 반복되는 무늬·점선·괘선은 무시하라.
- 읽을 수 없는 부분은 ⟦판독불가⟧ 로만 표기하라. 그럴듯한 값으로 채우지 마라.
- 도장·서명·로고·사진은 [직인] [서명] [로고] [사진] 으로만 표기하라.
- 숫자·날짜·코드(예: M17.1, 7급5111호, 주민번호)는 한 글자도 바꾸지 마라. 애매하면 ⟦판독불가⟧.
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
        "max_tokens": 4096,   # 무제한 생성 방지 — 반복 루프에 빠지면 응답이 영원히 안 끝난다
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


def fabrix_transcribe(png: bytes) -> str:
    """FabriX I2T 전사(§1 messages-with-models) — multipart, isStream=false.
    modelIds=[TEXT, I2T] 2종 필수, 응답은 camelCase JSON의 content."""
    import requests
    for k, v in (("FABRIX_BASE_URL", FABRIX_BASE_URL), ("FABRIX_CLIENT_KEY", FABRIX_CLIENT_KEY),
                 ("FABRIX_TEXT_MODEL_ID", FABRIX_TEXT_MODEL_ID),
                 ("FABRIX_I2T_MODEL_ID", FABRIX_I2T_MODEL_ID)):
        if not v:
            raise RuntimeError(f"{k} 미설정 — FabriX I2T 백엔드는 환경변수 4종+패스키가 필요합니다")
    token = FABRIX_PASS_KEY if FABRIX_PASS_KEY.startswith("Bearer ") else f"Bearer {FABRIX_PASS_KEY}"
    headers = {"x-fabrix-client": FABRIX_CLIENT_KEY, "x-openapi-token": token}
    if FABRIX_USER_EMAIL:
        headers["x-generative-ai-user-email"] = FABRIX_USER_EMAIL
    form = {"modelIds": [FABRIX_TEXT_MODEL_ID, FABRIX_I2T_MODEL_ID],
            "contents": [PROMPT], "isStream": "false",
            "llmConfig": json.dumps({"max_new_tokens": 4096, "temperature": 0.1})}
    r = requests.post(f"{FABRIX_BASE_URL}/openapi/chat/v1/messages-with-models",
                      headers=headers, data=form,
                      files={"files": ("tile.png", png, "image/png")}, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if data.get("status") not in (None, "SUCCESS"):
        reason = (data.get("filterBlockReason") or {}).get("ko") or data.get("responseCode")
        raise RuntimeError(f"FabriX 응답 비정상({data.get('status')}): {reason}")
    return (data.get("content") or "").strip()


def tess_transcribe(png: bytes) -> str:
    import io
    import pytesseract
    from PIL import Image
    return pytesseract.image_to_string(Image.open(io.BytesIO(png)), lang="kor+eng")


def mock_transcribe(png: bytes) -> str:
    return f"[MOCK 전사 — 이미지 {len(png)}바이트. 실행 환경에 비전 모델이 없어 파이프라인만 검증]"


_FILLER = re.compile(r"^[\s.\-_=~·•|:]*$")


def clean_transcript(text: str) -> str:
    """모델 반복 붕괴 제거: ①필러 줄(점·괘선만) 삭제 ②연속 동일 줄 2회로 축약
    ③'라벨:'/'값:' 메타 접두 제거(구버전 프롬프트 호환)."""
    out, prev, run = [], None, 0
    for ln in text.splitlines():
        t = ln.strip()
        if _FILLER.match(t):
            continue
        t = re.sub(r"^(라벨|값)\s*[:：]\s*", "", t)
        if not t:
            continue
        if t == prev:
            run += 1
            if run >= 2:      # 같은 줄 3번째부터 반복 붕괴로 간주
                continue
        else:
            prev, run = t, 0
        out.append(t)
    return "\n".join(out)


def _norm_line(l: str) -> str:
    """중복 판정용 정규화 — 공백 차이('( 의뢰일:' vs '(의뢰일:')로 같은 줄을
    다른 줄로 오판해 중복이 살아남던 실측 사례(04판독지 p3, 숫자CER 6배) 대응."""
    return re.sub(r"\s+", "", l)


def merge_tiles(parts: list[str], probe: int = 6) -> str:
    """타일 이어붙이기 — 겹침 구간(8%)이 양쪽 타일에 중복 전사되므로,
    다음 타일 앞부분에서 이전 타일 끝과 겹치는 줄 구간을 찾아 잘라낸다.
    비교는 공백 정규화 기준(전사 표기 흔들림 허용)."""
    merged: list[str] = []
    for part in parts:
        lines = [l for l in part.splitlines() if l.strip()]
        if merged and lines:
            tail = [_norm_line(l) for l in merged[-probe:]]
            head = [_norm_line(l) for l in lines[:probe]]
            cut = 0
            for k in range(min(probe, len(lines)), 0, -1):
                if head[:k] == tail[-k:]:
                    cut = k
                    break
            lines = lines[cut:]
        merged.extend(lines)
    return "\n".join(merged)


def sanity(text: str) -> list[str]:
    """VLM 환각의 흔한 형태를 기계 검사 — 통과 못 하면 담당자 검수 필수 표시."""
    warns = []
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for l in set(lines):
        if len(l) > 3 and lines.count(l) >= 5:
            warns.append(f"반복 루프 의심: '{l[:30]}' ×{lines.count(l)}")
    # 타일 이음새 중복: 근접(3줄 이내) 동일 줄(공백 무시) — 문서상 정당한 반복(섹션 재등장)은
    # 통상 멀리 떨어져 있어 오탐이 없다. 숫자 줄 중복은 숫자CER을 크게 부풀림(실측 p3).
    normed = [_norm_line(l) for l in lines]
    for i, n in enumerate(normed):
        if len(n) >= 8:
            for j in range(i + 1, min(i + 4, len(normed))):
                if n == normed[j]:
                    warns.append(f"근접 중복 줄(타일 이음새 의심): '{lines[i][:30]}'")
                    break
    if len(text) < 20:
        warns.append("전사량 과소 — 페이지 누락 의심")
    if re.search(r"(요약|정리하면|번역)\s*[:：]", text):
        warns.append("전사 외 생성(요약/번역) 의심")
    return list(dict.fromkeys(warns))


def main():
    ap = argparse.ArgumentParser(description="VLM 스캔 OCR 프로토타입")
    ap.add_argument("pdfs", nargs="*", help="스캔 PDF 경로")
    ap.add_argument("-o", "--out", default="out_vlm")
    ap.add_argument("--dpi", type=int, default=260)
    ap.add_argument("--tiles", type=int, default=3, help="페이지 세로 분할 수 (1=통짜 — 전장 A4는 글자 뭉개짐)")
    ap.add_argument("--backend", choices=["vlm", "fabrix", "tesseract", "mock"], default="vlm",
                    help="vlm=OpenAI 호환 비전 서빙 / fabrix=FabriX I2T(messages-with-models)")
    ap.add_argument("--compare", action="store_true", help="VLM과 tesseract를 모두 돌려 페이지별 비교")
    ap.add_argument("--selftest", action="store_true", help="이미지가 모델에 전달되는지 프로브 문구로 확인")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.pdfs:
        ap.error("PDF 경로를 지정하거나 --selftest 를 사용하세요")

    fn = {"vlm": vlm_transcribe, "fabrix": fabrix_transcribe,
          "tesseract": tess_transcribe, "mock": mock_transcribe}
    for pdf_path in args.pdfs:
        pdf = Path(pdf_path)
        dest = Path(args.out) / pdf.stem
        dest.mkdir(parents=True, exist_ok=True)
        pages, report = [], {"file": pdf.name, "backend": args.backend, "model": VLM_MODEL, "pages": []}
        for no, png in render_pages(pdf, args.dpi):
            t0 = time.time()
            if args.backend in ("vlm", "fabrix") and args.tiles > 1:
                parts = []
                for ti, t in enumerate(split_tiles(png, args.tiles), 1):
                    tt = time.time()
                    parts.append(fn[args.backend](t))
                    print(f"    p{no} tile{ti}/{args.tiles}: {round(time.time()-tt,1)}s")
                text = clean_transcript(merge_tiles(parts))   # 겹침 중복 제거 + 반복 붕괴 정리
            elif args.backend in ("vlm", "fabrix"):
                text = clean_transcript(fn[args.backend](png))
            else:
                text = fn[args.backend](png)
            entry = {"page": no, "chars": len(text), "sec": round(time.time() - t0, 1),
                     "warnings": sanity(text) if args.backend in ("vlm", "fabrix") else []}
            if args.compare and args.backend in ("vlm", "fabrix"):
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
