# -*- coding: utf-8 -*-
"""OCR LLM 정규화 — 모델 비교 실측 (실서버 모델 선정 근거).

같은 실데이터 블록 세트를 여러 모델에 돌려 필드 정확도·JSON 안정성·속도를 비교한다.
정답(expected)은 원문 수기 대조로 확정된 값 — 정기조(sd 15) 건 기준.

사용:
  python3 scripts/eval_normalize_models.py                          # 기본 3모델
  python3 scripts/eval_normalize_models.py exaone3.5:7.8b qwen3:30b-a3b
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_MODELS = ["exaone3.5:7.8b", "qwen3:30b-a3b", "gemma3:12b"]
EVAL_SD = 15   # 정기조 — 소견서·통보서·진단서·수술기록 혼합 7블록

# 수기 대조 정답 (블록 인덱스 → 기대 필드). 부분 일치 허용 값은 리스트.
EXPECTED = {
    0: {"disease": ["방광암"], "grade": ["6급2항", "5108호"], "exam_kind": ["재확인"]},
    1: {"disease": ["방광암"]},
    2: {"disease": ["방광암"], "kcd_fix": ["C67.9"]},   # 원문 오인식 '067.9' → C67.9 교정 기대
}
# grade에 절대 나오면 안 되는 값 (병리등급 혼동 검사)
GRADE_FORBIDDEN = ("G1", "G2", "G3")


def eval_model(model: str) -> dict:
    # 모델 교체: llm_client 모듈이 임포트해 둔 FABRIX_MODEL 상수를 직접 패치
    import core.llm_client as lc
    lc.FABRIX_MODEL = model
    from core.llm_client import get_llm
    from services.ocr_normalize import _block_text, _parse_json
    import psycopg
    from psycopg.rows import dict_row
    from config.settings import PG_DSN

    llm = get_llm()

    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT raw_text, exams FROM scan_doc WHERE sd_id=%s", (EVAL_SD,))
        sd = cur.fetchone()
    blocks = sd["exams"] if isinstance(sd["exams"], list) else json.loads(sd["exams"])
    raw_lines = sd["raw_text"].splitlines()

    r = {"model": model, "json_ok": 0, "json_fail": 0, "field_hit": 0, "field_miss": 0,
         "grade_confusion": 0, "kcd_fixed": 0, "sec_per_block": []}
    for i, b in enumerate(blocks):
        text = _block_text(raw_lines, blocks, i)
        t0 = time.time()
        try:
            out = llm.generate("ocr_normalize", doc_title=b.get("doc") or "의무기록",
                               ocr_text=text, corrections="(없음)")
            norm = _parse_json(out)
        except Exception:
            norm = None
        r["sec_per_block"].append(round(time.time() - t0, 1))
        if norm is None:
            r["json_fail"] += 1
            continue
        r["json_ok"] += 1
        exp = EXPECTED.get(i, {})
        for fld, needles in exp.items():
            if fld == "kcd_fix":
                joined = json.dumps(norm, ensure_ascii=False)
                if any(n in joined for n in needles):
                    r["kcd_fixed"] += 1
                continue
            v = str(norm.get(fld) or "")
            hit = all(n in v for n in needles) if fld == "grade" else any(n in v for n in needles)
            r["field_hit" if hit else "field_miss"] += 1
        g = str(norm.get("grade") or "")
        if any(x in g for x in GRADE_FORBIDDEN):
            r["grade_confusion"] += 1
    r["avg_sec"] = round(sum(r["sec_per_block"]) / max(len(r["sec_per_block"]), 1), 1)
    return r


def main():
    models = sys.argv[1:] or DEFAULT_MODELS
    print(f"평가 대상: sd {EVAL_SD} (블록 {len(EXPECTED)}건 정답 대조 + 전 블록 JSON·속도)\n")
    rows = []
    for m in models:
        print(f"── {m} 실행 중…")
        rows.append(eval_model(m))
    print(f"\n{'모델':22} {'JSON':>9} {'필드정답':>8} {'병리혼동':>6} {'KCD교정':>5} {'블록당(s)':>8}")
    for r in rows:
        print(f"{r['model']:22} {r['json_ok']}/{r['json_ok']+r['json_fail']:>4}"
              f" {r['field_hit']}/{r['field_hit']+r['field_miss']:>5}"
              f" {r['grade_confusion']:>8} {r['kcd_fixed']:>7} {r['avg_sec']:>8}")
    out = "scripts/eval_normalize_result.json"
    json.dump(rows, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"\n저장: {out}")


if __name__ == "__main__":
    main()
