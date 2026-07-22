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


def normalize_scan(sd_id: int, force: bool = False, llm=None) -> dict:
    """scan_doc 1건의 전 하위문서 블록 정규화. 이미 된 블록은 건너뜀(force=재실행)."""
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
    return {"sd_id": sd_id, "blocks": len(blocks),
            "llm": n_llm, "rule_fallback": n_rule, "skipped": n_skip}
