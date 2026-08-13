# -*- coding: utf-8 -*-
"""과거 심의 안건 → 유사사례 풀(cases) 백필 — 통합보훈 덤프 기반 (컬럼 명세 260813).

  python3 scripts/backfill_cases.py [--src <스테이징 DSN>] [--limit N] [--dry]

동작: 원천(RV_AGND) 전 안건 순회 → 심의·의결 텍스트 조립(link_collector.build_case_summary)
      → cases upsert(src_case_key 멱등) → 요약임베딩(bge-m3) 일괄 산출.
전제: 통합보훈 덤프가 스테이징 PostgreSQL에 원천 테이블명 그대로 적재돼 있을 것.
      (Oracle 직결은 oracledb 반입 후 접속부 교체)
주의: summary에 실명이 포함될 수 있어 적재 전 성명 마스킹은 원천 덤프 정제 단계에서
      선행돼야 함 — 본 스크립트는 주민번호 패턴만 방어적으로 제거한다.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg

from config.settings import LINK_SRC_DSN, PG_DSN
from ingestion.link_collector import Q_POLL_DEFAULT, build_case_summary, fetch_case

_RRN = re.compile(r"\d{6}[-\s]?\d{7}")


def main():
    ap = argparse.ArgumentParser(description="유사사례 풀 백필 (통합보훈 덤프 → cases)")
    ap.add_argument("--src", default=LINK_SRC_DSN, help="스테이징 DSN (기본 LINK_SRC_DSN)")
    ap.add_argument("--limit", type=int, help="처리 안건 수 상한 (검증용)")
    ap.add_argument("--dry", action="store_true", help="적재 없이 조립 결과만 표본 출력")
    a = ap.parse_args()
    if not a.src:
        ap.error("--src 또는 LINK_SRC_DSN 필요 (덤프 스테이징 DB)")

    with psycopg.connect(a.src) as src, src.cursor() as scur:
        scur.execute(Q_POLL_DEFAULT)
        keys = [str(r[0]) for r in scur.fetchall()]
        if a.limit:
            keys = keys[:a.limit]
        print(f"대상 안건 {len(keys)}건")

        rows = []
        for i, key in enumerate(keys, 1):
            b = fetch_case(scur, key)
            if not b.get("agnd"):
                continue
            c = build_case_summary(b)
            c["summary"] = _RRN.sub("******-*******", c["summary"] or "")
            if not c["summary"].strip():
                continue   # 텍스트 없는 안건은 사례 가치 없음
            rows.append(c)
            if i % 200 == 0:
                print(f"  조립 {i}/{len(keys)}")

    print(f"조립 완료 {len(rows)}건 (텍스트 없는 안건 제외)")
    if a.dry:
        for c in rows[:3]:
            print("──", c["src_case_key"], "|", (c["decision"] or "")[:40])
            print(c["summary"][:300])
        return

    from ingestion.embedder import get_embedder
    emb = get_embedder()
    vecs = emb.encode([c["summary"] for c in rows])
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        for c, v in zip(rows, vecs):
            cur.execute("""
                INSERT INTO cases (review_type, review_content, exam_category, decision,
                                   decided_at, summary, summary_embedding, duty_type, src_case_key)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (src_case_key) WHERE src_case_key IS NOT NULL DO UPDATE SET
                  review_type=EXCLUDED.review_type, review_content=EXCLUDED.review_content,
                  exam_category=EXCLUDED.exam_category, decision=EXCLUDED.decision,
                  summary=EXCLUDED.summary, summary_embedding=EXCLUDED.summary_embedding,
                  duty_type=EXCLUDED.duty_type""",
                (c["review_type"], c["review_content"], c["exam_category"], c["decision"],
                 c["decided_at"], c["summary"], v, c["duty_type"], c["src_case_key"]))
        conn.commit()
    print(f"cases 적재 {len(rows)}건 완료 (src_case_key 멱등)")


if __name__ == "__main__":
    main()
