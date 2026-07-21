# -*- coding: utf-8 -*-
"""스캔 문서 OCR 정규화 일괄 실행 — services/ocr_normalize.py 배치 구동.

사용:
  python3 scripts/normalize_scans.py            # 실데이터 전체 (미정규화 블록만)
  python3 scripts/normalize_scans.py --all      # 시연 스캔 포함 전체
  python3 scripts/normalize_scans.py --force    # 재정규화
  python3 scripts/normalize_scans.py 4 7 9      # 특정 sd_id만

내부망에서는 FABRIX_ENDPOINT 설정 시 FabriX가 정제 담당,
미설정·불가 시 규칙 폴백(norm.source='rule')으로도 항상 결과를 남긴다.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*", type=int)
    ap.add_argument("--all", action="store_true", help="실데이터 외 스캔도 포함")
    ap.add_argument("--force", action="store_true", help="이미 정규화된 블록도 재실행")
    args = ap.parse_args()

    import psycopg
    from config.settings import PG_DSN
    from services.ocr_normalize import normalize_scan

    ids = args.ids
    if not ids:
        with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
            cur.execute("SELECT sd_id FROM scan_doc" + ("" if args.all else " WHERE is_real")
                        + " ORDER BY sd_id")
            ids = [r[0] for r in cur.fetchall()]
    tot = {"llm": 0, "rule_fallback": 0, "skipped": 0}
    for sid in ids:
        r = normalize_scan(sid, force=args.force)
        if "error" in r:
            print(f"[{sid}] 오류: {r['error']}")
            continue
        for k in tot:
            tot[k] += r[k]
        print(f"[{sid}] 블록 {r['blocks']} — LLM {r['llm']} / 규칙폴백 {r['rule_fallback']} / 건너뜀 {r['skipped']}")
    print(f"== 합계: LLM {tot['llm']} / 규칙폴백 {tot['rule_fallback']} / 건너뜀 {tot['skipped']}")


if __name__ == "__main__":
    main()
