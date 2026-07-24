# -*- coding: utf-8 -*-
"""bohun_ocr_refine 정제 산출물(final.json) → scan_doc 적재.

ocr_refine 파이프라인(마스킹→분할→LLM 교정)이 만든 out/<파일명>/final.json을
읽어 적재한다. raw_text에는 '교정 전문'이 들어가므로 이후 정규화·정리본·챗봇
적재가 모두 교정본 기준으로 동작한다 (원본 txt는 ocr_refine 쪽에 보존).

사용:
  python3 scripts/ingest_refined.py <ocr_refine의 out 폴더>
  python3 scripts/ingest_refined.py ~/projects/bohun_ocr_refine/out
적재 후 후속 (필요 시):
  python3 scripts/to_grade_all.py && python3 db/build_instance_graph.py
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def ingest_final(fp: Path) -> tuple[int, dict]:
    import psycopg
    from config.settings import PG_DSN

    final = json.loads(fp.read_text(encoding="utf-8"))
    # 블록: 기존 scan_doc 소비자(정규화·안건 변환·그래프)와 호환 형태로 매핑
    blocks, parts = [], []
    for d in final["docs"]:
        fields = {k: v for k, v in d.items()
                  if k in ("disease", "grade", "exam_kind", "date", "kcd", "person", "hospital")}
        if isinstance(fields.get("disease"), list):
            fields["disease"] = ", ".join(fields["disease"])
        blocks.append({"doc": d["doc_type"], "line": d["line_range"][0],
                       "fields": fields,
                       "corrected": bool(d.get("fixes")), "n_fixes": len(d.get("fixes") or []),
                       "kcd_check": d.get("kcd_check"), "kcd_suggest": d.get("kcd_suggest"),
                       "excerpt": (d.get("corrected") or "")[:400]})
        parts.append(d.get("corrected") or "")
    corrected_text = "\n\n".join(parts)

    disease = next((b["fields"].get("disease") for b in blocks if b["fields"].get("disease")), None)
    hospital = next((b["fields"].get("hospital") for b in blocks if b["fields"].get("hospital")), None)
    kind = (blocks[0]["doc"] if len(blocks) == 1
            else f"의무기록 묶음({disease or str(len(blocks)) + '건'})")

    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM scan_doc WHERE file_name=%s", (final["source"],))
        cur.execute(
            "INSERT INTO scan_doc(reg_no, person, hospital, doc_kind, file_name, orig_path,"
            " pages, ocr_used, raw_text, exams, is_real)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,true,%s,%s,true) RETURNING sd_id",
            (final.get("birth"), final.get("person"), hospital, kind, final["source"],
             str(fp), len(blocks), corrected_text,
             json.dumps(blocks, ensure_ascii=False)))
        sd_id = cur.fetchone()[0]
        conn.commit()
    return sd_id, final


def main():
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "../bohun_ocr_refine/out")
    finals = sorted(out_dir.glob("*/final.json"))
    if not finals:
        sys.exit(f"final.json 없음: {out_dir} — ocr_refine을 --correct로 먼저 실행하세요")
    ok = 0
    for fp in finals:
        try:
            sd_id, final = ingest_final(fp)
            fixes = final.get("n_fixes", 0)
            print(f"[sd {sd_id}] {final.get('person') or '?'} — 하위문서 {final['n_docs']}건,"
                  f" 교정 {fixes}건")
            ok += 1
        except Exception as e:
            print(f"실패: {fp.parent.name} — {e}", file=sys.stderr)
    print(f"\n적재 완료: {ok}/{len(finals)}건. 후속: scripts/to_grade_all.py → db/build_instance_graph.py")


if __name__ == "__main__":
    main()
