# -*- coding: utf-8 -*-
"""미변환 실데이터 스캔 문서 전체 → 상이등급 안건 일괄 변환 (배포 서버용 CLI).

사용 (레포 루트에서):
  ./.venv/bin/python scripts/to_grade_all.py

전제: scan_doc에 실데이터가 적재돼 있을 것 — 웹 /ocr.html 업로드 또는
  python3 scripts/ocr_ingest_scans.py --dir <txt폴더>
웹에서는 OCR 페이지의 "미변환 전체 → 상이등급 안건 일괄 변환" 버튼과 동일 동작.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    import psycopg
    from config.settings import PG_DSN
    from services import scan_to_case

    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT sd_id, person FROM scan_doc"
                    " WHERE is_real AND ga_id IS NULL ORDER BY sd_id")
        targets = cur.fetchall()
    if not targets:
        print("변환 대상 없음 — 스캔 문서가 없거나 이미 전부 안건에 연결됨")
        return

    ok, skip = 0, []
    for sd_id, person in targets:
        try:
            r = scan_to_case.to_grade(sd_id)
            if "error" in r:
                skip.append((sd_id, person, r["error"]))
                continue
            tag = "기존연결" if r.get("existed") else "신규"
            print(f"[sd {sd_id}] {person} → 안건 {r.get('agenda_no', r['ga_id'])} ({tag})")
            ok += 1
        except Exception as e:
            skip.append((sd_id, person, str(e)[:100]))
    print(f"\n완료: 변환 {ok}건, 건너뜀 {len(skip)}건")
    for s in skip:
        print("  건너뜀:", *s)


if __name__ == "__main__":
    main()
