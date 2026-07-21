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


# ── 실데이터 OCR txt(기 OCR 완료 묶음) 파싱 ─────────────────────────
# 신체검사 소견서·고엽제 검진결과통보서·진단서·판독지·외래기록 등이 한 파일에
# 이어진 형태. 서식 제목 라인을 앵커로 하위문서를 분할하고 핵심 필드를 추출한다.
DOC_TITLES = [
    "신체검사 의사 소견서", "고엽제후유의증환자등검진결과통보서", "검진결과통보서",
    "고엽제후유의증환자등", "진 단서", "진단서", "입 퇴 원 요 약", "입퇴원요약",
    "외래재진기록", "외래기록", "외 래 기 록", "방사선 판독", "수술기록",
    "PULMONARY FUNCTION TEST", "경과기록지", "답변서", "사실조사", "의무조사보고서",
]
RE_RRN = re.compile(r"\b(\d{6})[- ]?([1-4](?:\d{6}|\*{6}))\b")
RE_KCD = re.compile(r"\b([A-Z]\d{2}(?:\.\d{1,2})?)\b")
RE_GRADE = re.compile(r"(\d급\s?\d?항?\s?\d{4}호|\d-\d-\d{4}|\d급)")
RE_ANYDATE = re.compile(r"(\d{4})[.\-/년\s]{1,2}(\d{1,2})[.\-/월\s]{1,2}(\d{1,2})")


def _find_after(lines, i, pats, span=3):
    """라벨 라인 i 다음 span줄 안에서 첫 비어있지 않은 값 줄."""
    for ln in lines[i + 1:i + 1 + span]:
        s = ln.strip()
        if s and not any(p in s for p in pats):
            return s
    return None


def parse_real_bundle(text):
    """실데이터 OCR 묶음 → 하위문서 블록 + 핵심 필드."""
    lines = text.splitlines()
    anchors = []  # (줄번호, 서식제목)
    for i, ln in enumerate(lines):
        s = ln.strip().replace(" ", "")
        for t in DOC_TITLES:
            if s.startswith(t.replace(" ", "")) and len(s) < len(t.replace(" ", "")) + 12:
                anchors.append((i, t.replace(" ", "") if t in ("진 단서", "입 퇴 원 요 약", "외 래 기 록") else t))
                break
    if not anchors:
        anchors = [(0, "의무기록")]
    blocks = []
    for bi, (ai, title) in enumerate(anchors):
        end = anchors[bi + 1][0] if bi + 1 < len(anchors) else len(lines)
        seg = lines[ai:end]
        body = "\n".join(seg)
        f = {}
        m = RE_RRN.search(body)
        if m:
            f["rrn"] = f"{m.group(1)}-{m.group(2)[0]}******"  # 저장 시점부터 마스킹
        for i, ln in enumerate(seg):
            s = ln.strip()
            if ("상이처(질병명)" in s or "신청질병명" in s or "질병명" == s) and "disease" not in f:
                v = _find_after(seg, i, ("질병명", "구분", "("))
                if v:
                    f["disease"] = v[:60]
            elif "신검과목" in s and "exam_dept" not in f:
                v = _find_after(seg, i, ("신검", "소속"))
                if v:
                    f["exam_dept"] = v[:20]
        g = RE_GRADE.search(body)
        if g:
            f["grade"] = g.group(1)
        k = RE_KCD.search(body)
        if k and title in ("진단서", "진 단서".replace(" ", "")):
            f["kcd"] = k.group(1)
        for d in RE_ANYDATE.finditer(body):  # OCR 잡음 배제 — 유효 범위의 첫 날짜만
            y, mo, dy = int(d.group(1)), int(d.group(2)), int(d.group(3))
            if 1940 <= y <= 2035 and 1 <= mo <= 12 and 1 <= dy <= 31:
                f["date"] = f"{y}-{mo:02d}-{dy:02d}"
                break
        blocks.append({"doc": title, "line": ai + 1, "fields": f,
                       "excerpt": " ".join(x.strip() for x in seg[:40] if x.strip())[:400]})
    return blocks


# 성명으로 볼 수 없는 라벨 단어들 (OCR 표 구조가 흐트러져 값 자리에 라벨이 오는 경우)
_NOT_NAME = ("성별", "성명", "주민", "번호", "나이", "보훈", "관할", "신규", "재심")


def _valid_name(v):
    v = (v or "").replace(" ", "").lstrip("고.").lstrip("故").strip(".")
    return v if (re.fullmatch(r"[가-힣]{2,4}", v) and v not in _NOT_NAME) else None


def person_from_filename(fname):
    """파일명에서 성명 폴백 — '오인규(441029)_...', '제출서류(故권영락)_...', '최승락88-근전도'.
    macOS 파일명은 NFD(자모 분해)라 반드시 NFC 정규화 후 매칭."""
    import unicodedata
    s = unicodedata.normalize("NFC", fname)
    m = re.search(r"[(\[]\s*故?\s*([가-힣]{2,4})\s*[)\]]", s)  # 괄호 안 (故권영락)
    if m and _valid_name(m.group(1)):
        return _valid_name(m.group(1))
    m = re.match(r"^故?([가-힣]{2,4})", s)                      # 선두 한글 런
    if m and _valid_name(m.group(1)) and m.group(1) not in ("제출서류", "진단서"):
        return _valid_name(m.group(1))
    return None


def _valid_disease(v):
    """질병명 값 검증 — 라벨 문구·번호 머리 제거, 라벨이면 무효."""
    v = re.sub(r"^\d+[.)]\s*", "", (v or "").strip())
    if len(v) < 2 or re.search(r"신체검사|의사\s*소견|소견서|질병명|상이처|구\s*분", v):
        return None
    return v[:60]


def ingest_real_txt(path):
    """기 OCR 완료된 실데이터 txt 묶음 적재 (is_real=true)."""
    import psycopg
    from config.settings import PG_DSN

    text = open(path, encoding="utf-8", errors="replace").read()
    # 등록번호(생년 6자리)는 마스킹 전 원문에서 추출 — 마스킹 후엔 \b 경계가 깨져 매칭 불가
    m = RE_RRN.search(text)
    # 저장 전 주민번호 뒷자리 마스킹 (실데이터 원문 보호 — 원본 파일은 별도 보존)
    masked = RE_RRN.sub(lambda mm: f"{mm.group(1)}-{mm.group(2)[0]}******", text)
    blocks = parse_real_bundle(masked)
    for b in blocks:  # 질병명 라벨 오추출 정리
        if "disease" in b["fields"]:
            d = _valid_disease(b["fields"]["disease"])
            if d:
                b["fields"]["disease"] = d
            else:
                del b["fields"]["disease"]
    lines = masked.splitlines()
    person = None
    for i, ln in enumerate(lines[:80]):
        if "성명" in ln:
            person = _valid_name(_find_after(lines, i, ("성명", "주민", "번호")))
            if person:
                break
    person = person or person_from_filename(os.path.basename(path))
    disease = next((b["fields"].get("disease") for b in blocks if b["fields"].get("disease")), None)

    os.makedirs(SCAN_DIR, exist_ok=True)
    fname = os.path.basename(path)
    dest = os.path.join(SCAN_DIR, fname)
    if os.path.abspath(path) != os.path.abspath(dest):
        shutil.copy2(path, dest)

    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM scan_doc WHERE file_name=%s", (fname,))
        cur.execute(
            "INSERT INTO scan_doc(reg_no, person, sex_age, hospital, doc_kind, file_name,"
            " orig_path, pages, ocr_used, raw_text, exams, is_real)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,true,%s,%s,true) RETURNING sd_id",
            (m.group(1) if m else None, person, None,
             next((h for h in ("부산보훈병원", "중앙보훈병원", "보훈병원") if h in masked), None),
             f"의무기록 묶음({disease or '실데이터'})", fname, dest, len(blocks),
             masked, json.dumps(blocks, ensure_ascii=False)))
        sd_id = cur.fetchone()[0]
        conn.commit()
    return sd_id, person, disease, len(blocks)


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
                        if f.lower().endswith((".pdf", ".txt")))
    if not files:
        ap.error("PDF/txt 파일 또는 --dir 폴더를 지정하세요")

    ok = 0
    for f in files:
        try:
            if f.lower().endswith(".txt"):  # 기 OCR 완료된 실데이터 묶음
                sd_id, person, disease, nblk = ingest_real_txt(f)
                print(f"[{sd_id}] {os.path.basename(f)} → {person} [실데이터] "
                      f"{disease or ''} 하위문서 {nblk}건")
            else:
                sd_id, head, nblk, ocr = ingest(f, args.dpi)
                print(f"[{sd_id}] {os.path.basename(f)} → {head['person']}({head['reg_no']}) "
                      f"{head['doc_kind']} 검사 {nblk}건 {'OCR' if ocr else '텍스트층'}")
            ok += 1
        except Exception as e:
            print(f"실패: {f} — {e}", file=sys.stderr)
    print(f"완료: {ok}/{len(files)}건 적재")


if __name__ == "__main__":
    main()
