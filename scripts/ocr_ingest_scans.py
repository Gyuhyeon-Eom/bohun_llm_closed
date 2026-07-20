# -*- coding: utf-8 -*-
"""신체검사 서류 스캔본 OCR 적재 — 스캔 PDF → 텍스트 → 정형 파싱 → scan_doc 테이블.

사용:
  python3 scripts/ocr_ingest_scans.py 파일1.pdf 파일2.pdf ...
  python3 scripts/ocr_ingest_scans.py --dir data/scans        # 폴더 일괄

동작:
  1. PDF에 텍스트층이 있으면 그대로 추출(정확), 없으면 tesseract OCR (kor+eng, 300dpi)
  2. 헤더(등록번호·성명·성별/나이) + 검사 블록(의뢰/검사/판독일, 임상진단명, 검사명,
     [Finding]/[Conclusion]/[Recommendation], 판독의) 정형 파싱
     — 이탤릭 제목은 OCR이 깨져도 날짜 3종 라인을 블록 앵커로 써서 견고하게 분리
  3. 원본 PDF를 data/originals/scans/ 에 보존 (출처 열람·다운로드용)
  4. scan_doc 적재 (같은 파일명 재실행 시 대체) → services/scan_to_case.py 로 사건 변환

필요물 (폐쇄망 반입 목록 포함):
  - Python: pymupdf, pytesseract (requirements.txt)
  - 시스템: tesseract-ocr + tesseract-ocr-kor (리눅스 .deb / 윈도우 설치본)
"""
import argparse
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SCAN_DIR = os.path.join("data", "originals", "scans")

# ── 정규식 (OCR 잡음 허용) ──────────────────────────────────────────
RE_DATE = re.compile(r"\d{4}-\d{2}-\d{2}(?:\s?\d{2}:\d{2})?")
RE_REG = re.compile(r"\(\s*(\d{7,9})\s*\)")
RE_NAME = re.compile(r"성명\s*[:;]?\s*\(?\s*([가-힣]{2,5})")
RE_SEXAGE = re.compile(r"\(?\s*([남여])\s*/\s*([\d.]+)\s*\)?")
RE_READER = re.compile(r"판독의\s*[:;]?\s*([가-힣]{2,5})\s*\(?\s*(\d+)?")
RE_MARK = {  # 대괄호 소실 허용
    "finding": re.compile(r"\[?\s*Finding\s*\]?", re.I),
    "conclusion": re.compile(r"\[?\s*Conclusion\s*\]?", re.I),
    "recommendation": re.compile(r"\[?\s*Recommendation\s*\]?", re.I),
}


def extract_text(path, dpi=200):
    # dpi 기본 200: 보훈병원 스캔본 실측 결과 200이 최적 (300·400은 저해상 원본의
    # 노이즈가 증폭되어 한글 굵은 글씨(판독의·검사명) 인식률이 오히려 하락)
    """페이지별 텍스트 리스트. 텍스트층 우선, 빈약하면 OCR."""
    import fitz
    doc = fitz.open(path)
    texts = [p.get_text() for p in doc]
    ocr_used = False
    if sum(len(t.strip()) for t in texts) < 30 * len(doc):  # 사실상 이미지 스캔
        import io

        import pytesseract
        from PIL import Image
        ocr_used = True
        texts = []
        for p in doc:
            pix = p.get_pixmap(dpi=dpi)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            texts.append(pytesseract.image_to_string(img, lang="kor+eng"))
    n = len(doc)
    doc.close()
    return texts, n, ocr_used


def _between(lines, i_start, stop_res):
    """i_start 다음 줄부터 stop 마커 전까지의 내용 줄들."""
    out = []
    for ln in lines[i_start + 1:]:
        if any(r.search(ln) for r in stop_res):
            break
        if ln.strip():
            out.append(ln.strip())
    return " ".join(out)


def parse_blocks(texts):
    """검사 블록 파싱. 날짜 3종( 의뢰일/검사일/판독일 ) 라인을 앵커로 분할."""
    # 페이지 경계 추적을 위해 (줄, 페이지번호) 시퀀스로 평탄화
    flat = []
    for pno, t in enumerate(texts, 1):
        for ln in t.splitlines():
            flat.append((ln, pno))
    anchors = [i for i, (ln, _) in enumerate(flat)
               if len(RE_DATE.findall(ln)) >= 2]  # 의뢰일/검사일(/판독일) 라인
    blocks = []
    for bi, ai in enumerate(anchors):
        end = anchors[bi + 1] if bi + 1 < len(anchors) else len(flat)
        seg = flat[ai:end]
        lines = [ln for ln, _ in seg]
        dates = RE_DATE.findall(lines[0])
        b = {"page": seg[0][1],
             "req_date": dates[0] if dates else None,
             "exam_date": dates[1] if len(dates) > 1 else None,
             "read_date": dates[2] if len(dates) > 2 else None,
             "dx": None, "exam_name": None, "finding": None,
             "conclusion": None, "recommendation": None, "reader": None}
        for i, ln in enumerate(lines):
            if "임상진단" in ln and b["dx"] is None:
                b["dx"] = _between(lines, i, [re.compile("임상소견"), re.compile("검사명")])
            elif "검사명" in ln and b["exam_name"] is None:
                b["exam_name"] = _between(lines, i, [RE_MARK["finding"]])
            elif RE_MARK["finding"].search(ln) and b["finding"] is None:
                b["finding"] = _between(lines, i, [RE_MARK["conclusion"]])
            elif RE_MARK["conclusion"].search(ln) and b["conclusion"] is None:
                b["conclusion"] = _between(lines, i, [RE_MARK["recommendation"]])
            elif RE_MARK["recommendation"].search(ln) and b["recommendation"] is None:
                b["recommendation"] = _between(lines, i, [re.compile("판독의")])
        # 폴백 ①: 검사명 헤더가 OCR로 깨진 경우 — [Finding] 직전의 라틴 라인이 검사명
        if not b["exam_name"] and b["finding"] is not None:
            fi = next((i for i, ln in enumerate(lines) if RE_MARK["finding"].search(ln)), 0)
            for ln in reversed(lines[:fi]):
                s = ln.strip()
                if (re.fullmatch(r"[A-Za-z][A-Za-z0-9 ,&()\-\./]+", s)
                        and s.lower() != (b["dx"] or "").strip().lower()):
                    b["exam_name"] = s
                    break
        # 폴백 ②: 판독의 — 정상 패턴 우선, 깨진 경우 사번 괄호 (NNNNN) 라인에서 복원
        for ln in lines:
            m = RE_READER.search(ln)
            if m:
                b["reader"] = m.group(1).replace(" ", "")
                break
        if b["reader"] is None:
            for ln in lines:
                m = re.search(r"([가-힣][가-힣\s]{1,6})?\(?\s*(\d{5,6})\s*\)\s*$", ln.strip())
                if m and ("판독" in ln or m.group(1)):
                    nm = (m.group(1) or "").replace(" ", "")
                    b["reader"] = f"{nm}({m.group(2)})" if nm else f"판독의({m.group(2)})"
                    break
        if b["exam_name"] or b["finding"]:
            blocks.append(b)
    return blocks


def parse_header(texts):
    head = "\n".join(texts[0].splitlines()[:12])
    reg = RE_REG.search(head)
    name = RE_NAME.search(head)
    sexage = RE_SEXAGE.search(head.split("성별", 1)[-1]) if "성별" in head else None
    full = "\f".join(texts)
    hospital = None
    for h in ("중앙보훈병원", "부산보훈병원", "광주보훈병원", "대구보훈병원", "대전보훈병원", "인천보훈병원", "보훈병원"):
        if h in full:
            hospital = h
            break
    kind = "영상검사결과" if ("검사명" in full and RE_MARK["finding"].search(full)) else "의무기록"
    return {"reg_no": reg.group(1) if reg else None,
            "person": name.group(1) if name else None,
            "sex_age": f"{sexage.group(1)}/{sexage.group(2)}" if sexage else None,
            "hospital": hospital, "doc_kind": kind}


def ingest(path, dpi=200):
    import psycopg
    from config.settings import PG_DSN

    texts, pages, ocr_used = extract_text(path, dpi)
    head = parse_header(texts)
    blocks = parse_blocks(texts)

    os.makedirs(SCAN_DIR, exist_ok=True)
    fname = os.path.basename(path)
    dest = os.path.join(SCAN_DIR, fname)
    if os.path.abspath(path) != os.path.abspath(dest):
        shutil.copy2(path, dest)

    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM scan_doc WHERE file_name=%s", (fname,))
        cur.execute(
            "INSERT INTO scan_doc(reg_no, person, sex_age, hospital, doc_kind, file_name,"
            " orig_path, pages, ocr_used, raw_text, exams)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING sd_id",
            (head["reg_no"], head["person"], head["sex_age"], head["hospital"],
             head["doc_kind"], fname, dest, pages, ocr_used,
             "\f".join(texts), json.dumps(blocks, ensure_ascii=False)))
        sd_id = cur.fetchone()[0]
        conn.commit()
    return sd_id, head, len(blocks), ocr_used


def main():
    ap = argparse.ArgumentParser(description="스캔 PDF OCR 적재")
    ap.add_argument("files", nargs="*", help="PDF 파일들")
    ap.add_argument("--dir", help="폴더 내 *.pdf 일괄")
    ap.add_argument("--dpi", type=int, default=200)
    args = ap.parse_args()

    files = list(args.files)
    if args.dir:
        files += sorted(os.path.join(args.dir, f) for f in os.listdir(args.dir)
                        if f.lower().endswith(".pdf"))
    if not files:
        ap.error("PDF 파일 또는 --dir 폴더를 지정하세요")

    ok = 0
    for f in files:
        try:
            sd_id, head, nblk, ocr = ingest(f, args.dpi)
            print(f"[{sd_id}] {os.path.basename(f)} → {head['person']}({head['reg_no']}) "
                  f"{head['doc_kind']} 검사 {nblk}건 {'OCR' if ocr else '텍스트층'}")
            ok += 1
        except Exception as e:
            print(f"실패: {f} — {e}", file=sys.stderr)
    print(f"완료: {ok}/{len(files)}건 적재")


if __name__ == "__main__":
    main()
