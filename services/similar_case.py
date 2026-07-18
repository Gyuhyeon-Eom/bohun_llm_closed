"""기능③ 유사사례 추천. 임베딩 벡터 검색 주력 + 심의유형·KCD 필터."""
import psycopg
from config.settings import PG_DSN


def find_similar(summary_vec: list[float], review_type: str | None = None,
                 kcd_codes: list[str] | None = None, n: int = 5) -> list[dict]:
    where, params = [], {"vec": summary_vec, "n": n}
    if review_type:
        where.append("review_type = %(rt)s"); params["rt"] = review_type
    if kcd_codes:
        where.append("kcd_codes && %(kcds)s"); params["kcds"] = kcd_codes  # 배열 교집합
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
    SELECT case_id, review_type, exam_category, kcd_codes, decision, decided_at, summary,
           round((1 - (summary_embedding <=> %(vec)s::vector))::numeric, 4) AS similarity
    FROM cases {where_sql}
    ORDER BY summary_embedding <=> %(vec)s::vector LIMIT %(n)s
    """
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
