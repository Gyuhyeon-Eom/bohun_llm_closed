"""시행령 [별표3] 상이등급구분표(data/laws/grade_criteria_annex3.json) -> grade_criteria 적재.

원문 출처: 국가유공자 등 예우 및 지원에 관한 법률 시행령 [별표 3] <개정 2023. 5. 23.>
(T/F 제공 법령 전문 텍스트에서 파싱한 189개 분류번호. 파싱 검증: 분류번호 중복 0, 빈 기준문 0)
실행: python3 scripts/ingest_grade_criteria.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import psycopg

from config.settings import PG_DSN
from ingestion.embedder import get_embedder

SRC = Path(__file__).parent.parent / "data" / "laws" / "grade_criteria_annex3.json"


def main():
    data = json.loads(SRC.read_text(encoding="utf-8"))
    rows = data["rows"]
    emb = get_embedder()
    # 검색 문장: 부위 + 기준문 (상병명 질의와의 임베딩 매칭용)
    vecs = emb.encode([f"{r['body_part']} {r['description']}" for r in rows])
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE grade_criteria")
        for r, v in zip(rows, vecs):
            cur.execute(
                "INSERT INTO grade_criteria(class_no, grade, section, body_part, description, embedding)"
                " VALUES (%s,%s,%s,%s,%s,%s)",
                (r["class_no"], r["grade"], r["section"], r["body_part"], r["description"], v))
        cur.execute("SELECT body_part, count(*) FROM grade_criteria GROUP BY 1 ORDER BY 1")
        for part, n in cur.fetchall():
            print(f"  {part}: {n}개")
    print(f"별표3 상이등급 기준 {len(rows)}개 적재 완료 ({data['source']})")


if __name__ == "__main__":
    main()
