# -*- coding: utf-8 -*-
"""OCR 텍스트 LLM 정규화 — 스캔 하위문서를 구조화 필드로 정제 (260721 회의 반영).

회의 지적: "스캔자료를 통째로 추출해 넣은 상태라 의미 있는 부분만 추려내는
정제가 안 되어 있다" / "어떤 부분을 근거로 판단했는지 추적이 어렵다".

처리: scan_doc의 하위문서 블록마다 원문 구간(라인 범위 보존)을 LLM에 넣어
doc_type·병명·등급·신검종류·소견·핵심 근거문장(key_findings)을 JSON으로 추출,
블록의 norm 필드에 저장한다. 원문 라인 번호가 남으므로 근거 추적이 가능하다.

원칙: LLM은 정제·요약만 — 원문에 없는 사실 생성 금지(프롬프트 강제).
LLM 불가·JSON 불일치 시 규칙 기반 폴백(norm.source='rule')으로 항상 결과를 남긴다.
"""
import json
import re
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from config.settings import PG_DSN

_NOISE = set("·¸'˙‚ˈ|■◆▶※")


def _block_text(raw_lines, blocks, idx, max_chars=3600):
    """블록 idx의 원문 구간 (시작 라인 ~ 다음 블록 시작 전)."""
    start = max(0, (blocks[idx].get("line") or 1) - 1)
    end = (blocks[idx + 1].get("line") - 1) if idx + 1 < len(blocks) else len(raw_lines)
    txt = "\n".join(ln for ln in raw_lines[start:end] if ln.strip())
    return txt[:max_chars]


def _parse_json(s):
    s = re.sub(r"^```(json)?|```$", "", (s or "").strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", s, re.S)  # 앞뒤 잡담 방어 — 첫 { ~ 끝 }
    return json.loads(m.group(0)) if m else None


def _rule_norm(block, text):
    """LLM 불가 시 폴백 — 이미 추출된 필드 + 잡음 제거 발췌. 추적 라인은 동일하게 보존."""
    f = block.get("fields") or {}
    clean = "".join(c for c in text if c not in _NOISE)
    sents = [s.strip() for s in re.split(r"[\n.]", clean)
             if len(s.strip()) > 10 and re.search(r"[가-힣]", s)]
    return {"doc_type": block.get("doc"), "date": f.get("date"), "hospital": None,
            "disease": f.get("disease"), "grade": f.get("grade"),
            "exam_kind": f.get("exam_kind"), "opinion": f.get("opinion"),
            "key_findings": sents[:3], "summary": (sents[0][:120] if sents else None),
            "source": "rule"}


def _recent_corrections(cur, n=5) -> str:
    """담당자 교정쌍(field_edit)을 few-shot 예시로 — 폐쇄망에서 모델 재학습 대신
    프롬프트 학습으로 교정 패턴을 반영하는 층 (수정이 쌓일수록 정규화가 좋아진다)."""
    cur.execute("""SELECT field, old_value, new_value FROM field_edit
                   WHERE old_value IS NOT NULL AND new_value IS NOT NULL
                     AND old_value <> new_value AND length(old_value) BETWEEN 2 AND 300
                   ORDER BY fe_id DESC LIMIT %s""", (n,))
    rows = cur.fetchall()
    if not rows:
        return "(축적된 교정 예시 없음)"
    return "\n".join(f"- [{r['field']}] \"{r['old_value'][:120]}\" → \"{r['new_value'][:120]}\""
                     for r in rows)


def clear_norms(sd_id: int) -> dict:
    """정규화 결과 제거 — 재정규화를 블록 단위 진행(limit)으로 돌리기 위한 선행 단계.
    (force+limit 조합은 매 호출 같은 블록을 재처리해 진행이 안 되므로, 지우고 스텝한다)"""
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT exams FROM scan_doc WHERE sd_id=%s", (sd_id,))
        sd = cur.fetchone()
        if not sd:
            return {"error": "스캔 문서 없음"}
        blocks = sd["exams"] if isinstance(sd["exams"], list) else json.loads(sd["exams"] or "[]")
        for b in blocks:
            b.pop("norm", None)
        cur.execute("UPDATE scan_doc SET exams=%s WHERE sd_id=%s",
                    (json.dumps(blocks, ensure_ascii=False), sd_id))
        conn.commit()
    return {"sd_id": sd_id, "cleared": len(blocks)}


def normalize_scan(sd_id: int, force: bool = False, llm=None, limit: int | None = None) -> dict:
    """scan_doc 1건의 하위문서 블록 정규화. 이미 된 블록은 건너뜀(force=재실행).
    limit: 이번 호출에서 처리할 최대 블록 수 — UI가 진행률을 보여주며 스텝 실행할 때 사용.
    반환 remaining이 0이 될 때까지 반복 호출하면 전체가 끝난다."""
    if llm is None:
        from core.llm_client import get_llm
        llm = get_llm()
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT sd_id, raw_text, exams FROM scan_doc WHERE sd_id=%s", (sd_id,))
        sd = cur.fetchone()
        if not sd:
            return {"error": "스캔 문서 없음"}
        blocks = sd["exams"] if isinstance(sd["exams"], list) else json.loads(sd["exams"] or "[]")
        if not blocks:
            return {"error": "하위문서 블록 없음"}
        raw_lines = (sd["raw_text"] or "").splitlines()
        corrections = _recent_corrections(cur)

        n_llm = n_rule = n_skip = 0
        for i, b in enumerate(blocks):
            if b.get("norm") and not force:
                n_skip += 1
                continue
            if limit is not None and (n_llm + n_rule) >= limit:
                break
            text = _block_text(raw_lines, blocks, i)
            norm = None
            try:
                out = llm.generate("ocr_normalize", doc_title=b.get("doc") or "의무기록",
                                   ocr_text=text, corrections=corrections)
                norm = _parse_json(out)
                if norm is not None:
                    norm["source"] = "llm"
                    n_llm += 1
            except Exception:
                norm = None
            if norm is None:
                norm = _rule_norm(b, text)
                n_rule += 1
            norm["src_line"] = b.get("line")  # 근거 추적: 원문 시작 라인
            b["norm"] = norm

        cur.execute("UPDATE scan_doc SET exams=%s WHERE sd_id=%s",
                    (json.dumps(blocks, ensure_ascii=False), sd_id))
        conn.commit()
    remaining = sum(1 for b in blocks if not b.get("norm"))
    return {"sd_id": sd_id, "blocks": len(blocks),
            "llm": n_llm, "rule_fallback": n_rule, "skipped": n_skip,
            "remaining": remaining}


# ── 정리본 (v0.2): 정규화 결과의 열람용 조립 - "원문 보기"의 기본 산출물 ──

def _build_sections(exams: list, doc_hospital: str | None) -> list[dict]:
    """블록별 norm(LLM 정규화) 우선, 파싱 필드 폴백으로 섹션을 결정적으로 조립.
    원문 라인(source_line)을 보존해 근거 추적이 끊기지 않게 한다."""
    sections = []
    for i, b in enumerate(exams or [], 1):
        f = b.get("fields") or {}
        n = b.get("norm") or {}
        sections.append({
            "seq": i,
            "doc_type": n.get("doc_type") or b.get("doc") or b.get("exam_name") or "미상",
            "date": n.get("date") or f.get("date") or b.get("exam_date"),
            "hospital": n.get("hospital") or doc_hospital,
            "disease": n.get("disease") or f.get("disease"),
            "grade": n.get("grade") or f.get("grade"),
            "exam_kind": n.get("exam_kind") or f.get("exam_kind"),
            "kcd": f.get("kcd"),
            "opinion": n.get("opinion") or f.get("opinion") or b.get("conclusion"),
            "summary": n.get("summary"),
            "key_findings": n.get("key_findings") or [],
            "reader": b.get("reader"),
            "source_line": b.get("line"), "page": b.get("page"),
            "normalized": bool(n), "norm_source": n.get("source"),
        })
    return sections


def clean_document(sd_id: int) -> dict | None:
    """스캔 문서의 정리본(JSON) - DB에 적재된 구조화 결과를 열람용으로 조립.
    결정적 조립만 수행하며 LLM을 호출하지 않는다. 미정규화 블록은 파싱 필드로 표시되고
    n_normalized로 정규화 진행 상태를 드러낸다 (화면에서 '재정규화' 유도)."""
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT sd_id, reg_no, person, sex_age, hospital, doc_kind, file_name,"
                    " pages, ocr_used, exams, app_id, is_real, created_at"
                    " FROM scan_doc WHERE sd_id=%s", (sd_id,))
        row = cur.fetchone()
    if not row:
        return None
    exams = row.pop("exams") or []
    if not isinstance(exams, list):
        exams = json.loads(exams or "[]")
    sections = _build_sections(exams, row.get("hospital"))
    return {**row, "n_sections": len(sections),
            "n_normalized": sum(1 for s in sections if s["normalized"]),
            "sections": sections}


def _section_lines(s: dict) -> list[str]:
    L = [f"{s['seq']}. {s['doc_type']}" + (f" ({s['date']})" if s.get("date") else "")]
    for key, label in (("disease", "질병명"), ("grade", "등급"), ("exam_kind", "신검종류"),
                       ("kcd", "KCD"), ("opinion", "소견"), ("reader", "판독의"),
                       ("summary", "요약")):
        if s.get(key):
            L.append(f"   {label}: {s[key]}")
    for kf in s.get("key_findings") or []:
        L.append(f"   근거: {kf}")
    if s.get("source_line"):
        L.append(f"   (원문 {s['source_line']}행~)")
    return L


def clean_text(doc: dict) -> str:
    """정리본 dict -> 열람·PDF용 평문 렌더 (decision_doc._text_to_pdf에 그대로 투입)."""
    L = [f"스캔 문서 정리본 [{doc['sd_id']}] {doc.get('person') or '성명 미상'}",
         f"문서종류: {doc.get('doc_kind') or '-'} / 병원: {doc.get('hospital') or '-'}"
         f" / 등록번호: {doc.get('reg_no') or '-'}",
         f"원본파일: {doc.get('file_name') or '-'} / 하위문서 {doc['n_sections']}건"
         f" (정규화 {doc['n_normalized']}건)", ""]
    for s in doc["sections"]:
        L.extend(_section_lines(s))
        L.append("")
    return "\n".join(L)


# ── 정리본 RAG 적재 (v0.2): 챗봇이 실데이터 스캔 내용을 검색할 수 있게 ──

def _clean_blocks(doc: dict):
    """정리본 -> ingestion Block 목록. 섹션 1개=블록 1개, page_no는 하위문서 순번
    (출처 표기 '[정리본 성명(sd) p.순번]'이 상세 화면의 섹션 번호와 일치)."""
    from ingestion.types import Block, BlockType
    header = (f"{doc.get('person') or '성명 미상'} {doc.get('doc_kind') or ''} "
              f"({doc.get('hospital') or '병원 미상'})")
    blocks = []
    for s in doc["sections"]:
        body = "\n".join([header] + _section_lines(s))
        blocks.append(Block(BlockType.PARAGRAPH, body, s["seq"], {"confidence": 1.0}))
    return blocks


def index_clean(sd_id: int, emb) -> dict:
    """정리본을 RAG 문서(chunks)로 적재. doc_type='스캔정리본'.
    재정규화로 내용이 바뀌면 기존 적재분을 교체(sha 다르면 삭제 후 재적재), 같으면 멱등 스킵."""
    from ingestion.chunker import chunk_blocks
    from ingestion.indexer import index_document, sha256_of
    doc = clean_document(sd_id)
    if not doc:
        return {"error": "스캔 문서 없음"}
    if not doc["n_sections"]:
        return {"error": "하위문서 블록 없음 - 적재 생략"}
    out = Path("data") / "clean_txt"
    out.mkdir(parents=True, exist_ok=True)
    txt = out / f"scan_{sd_id}.txt"
    txt.write_text(clean_text(doc), encoding="utf-8")
    label = f"정리본 {doc.get('person') or '성명미상'}({sd_id})"
    path = f"{txt}#{label}"
    digest = sha256_of(path)
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        # 구버전 교체 - 같은 정리본 파일에서 나온 이전 적재분(내용 변경분)만 삭제 (chunks는 FK cascade)
        cur.execute("DELETE FROM documents WHERE source_path LIKE %s AND sha256 <> %s",
                    (f"{txt}#%", digest))
        replaced = cur.rowcount
    chunks = chunk_blocks(_clean_blocks(doc))
    vecs = emb.encode([c.content for c in chunks])
    n = index_document(path, "스캔정리본", chunks, vecs, "clean-v1")
    return {"sd_id": sd_id, "chunks": n, "replaced": replaced,
            "skipped": n == 0, "label": label}
