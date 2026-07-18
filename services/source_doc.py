"""근거 원문 문서 열람·다운로드 (챗봇·AI검토의 출처 하이퍼링크용).

- 문서는 doc_id로만 접근 (documents 테이블에 등록된 경로만 서빙 — 경로 조작 차단)
- 텍스트 원문은 미리보기·다운로드 모두 개인정보 자동 마스킹을 거친다 (프로토타입 규칙:
  주민등록번호·전화번호·이메일 패턴. 운영에서는 권한 TB의 개인정보접근여부와 연계할 지점)
- PDF는 브라우저 내장 뷰어(#page=N)로 해당 페이지를 바로 연다. 실데이터(스캔 의무기록 등)
  반입 단계에서 PDF 마스킹은 별도 과제(레이어 삭제·라스터화)로 확인 필요.
"""
import os
import re
import tempfile
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from config.settings import PG_DSN

_MASKS = [
    (re.compile(r"\b\d{6}[- ]\d{7}\b"), "******-*******"),                    # 주민등록번호
    (re.compile(r"\b01\d[- ]?\d{3,4}[- ]?\d{4}\b"), "01*-****-****"),          # 휴대전화
    (re.compile(r"\b0\d{1,2}-\d{3,4}-\d{4}\b"), "0**-****-****"),              # 일반전화
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "****@****"),                     # 이메일
]


def mask(text: str) -> str:
    for pat, repl in _MASKS:
        text = pat.sub(repl, text)
    return text


def _row(doc_id: int) -> dict | None:
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT doc_id, doc_type, source_path, orig_path FROM documents WHERE doc_id=%s", (doc_id,))
        return cur.fetchone()


def _parse(source_path: str) -> tuple[Path, str]:
    """source_path('경로#표시명#논스' | '경로#태그' | '경로') -> (실제 경로, 표시명)."""
    parts = str(source_path).split("#")
    real = Path(parts[0])
    name = parts[1] if len(parts) > 1 and parts[1] else real.name
    return real, name


def load(doc_id: int) -> dict:
    """미리보기용 메타+내용. kind: text(마스킹 본문 포함) | pdf | missing.
    원본 스캔 파일(orig_path, 보통 PDF)이 있으면 그것을 원문으로 우선한다 —
    OCR 텍스트는 파생물이고 심사관이 봐야 할 '원문'은 스캔본이기 때문."""
    row = _row(doc_id)
    if not row:
        return {"kind": "missing", "detail": "등록되지 않은 문서"}
    if row.get("orig_path"):
        oreal, oname = _parse(row["orig_path"])
        if oreal.is_file() and oreal.suffix.lower() == ".pdf":
            return {"kind": "pdf", "doc_id": doc_id, "name": oname,
                    "doc_type": row["doc_type"], "scan": True}
    real, name = _parse(row["source_path"])
    if not real.is_file():
        return {"kind": "missing", "name": name, "doc_type": row["doc_type"],
                "detail": "원본 파일이 서버에 없음 (경로 이동·삭제)"}
    if real.suffix.lower() == ".pdf":
        return {"kind": "pdf", "doc_id": doc_id, "name": name, "doc_type": row["doc_type"]}
    try:
        text = real.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"kind": "missing", "name": name, "doc_type": row["doc_type"],
                "detail": "미리보기 미지원 형식(텍스트/PDF 외)"}
    return {"kind": "text", "doc_id": doc_id, "name": name, "doc_type": row["doc_type"],
            "text": mask(text), "masked": True}


def export_file(doc_id: int) -> tuple[str, str, str] | None:
    """다운로드/미리보기 파일. (파일명, 경로, media_type). 텍스트는 마스킹 적용본을 서빙."""
    row = _row(doc_id)
    if not row:
        return None
    if row.get("orig_path"):                       # 원본 스캔 PDF 우선
        oreal, oname = _parse(row["orig_path"])
        if oreal.is_file() and oreal.suffix.lower() == ".pdf":
            return (oname if oname.endswith(".pdf") else f"{oname}.pdf",
                    str(oreal), "application/pdf")
    real, name = _parse(row["source_path"])
    if not real.is_file():
        return None
    if real.suffix.lower() == ".pdf":
        return name if name.endswith(".pdf") else f"{name}.pdf", str(real), "application/pdf"
    try:
        text = mask(real.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        return None
    out = os.path.join(tempfile.gettempdir(), f"masked_{doc_id}_{Path(name).stem}.txt")
    with open(out, "w", encoding="utf-8-sig") as f:
        f.write(text)
    dl = name if name.endswith(".txt") else f"{name}.txt"
    return dl, out, "text/plain; charset=utf-8"
