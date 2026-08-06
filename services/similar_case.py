"""기능③ 유사사례 추천. 임베딩 벡터 검색 주력 + 심의유형·KCD 필터.

search_cases: 담당자 직접 검색(검토의견 39번·3분과) — 의결일자·키워드(AND/OR)·
소속·계급·결과를 복합(AND) 조건으로 검색. 의미 질의(query_vec) 지정 시 유사도순."""
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


def search_cases(keywords: list[str] | None = None, mode: str = "and",
                 date_from: str | None = None, date_to: str | None = None,
                 duty_type: str | None = None, person_rank: str | None = None,
                 decision: str | None = None, review_type: str | None = None,
                 query_vec: list[float] | None = None, n: int = 20) -> list[dict]:
    """유사사례 상세검색 — 조건 간 AND, 키워드끼리만 mode(and|or).
    키워드는 요약·심의내용·신검유형 텍스트 대상 부분일치(ILIKE).
    정렬: query_vec 있으면 유사도순, 없으면 의결일자 최신순."""
    where, params = [], {"n": max(1, min(int(n), 50))}
    for i, kw in enumerate(keywords or []):
        params[f"kw{i}"] = f"%{kw}%"
    if keywords:
        pieces = [f"(summary || ' ' || coalesce(review_content,'') || ' ' ||"
                  f" coalesce(exam_category,'')) ILIKE %(kw{i})s"
                  for i in range(len(keywords))]
        joiner = " OR " if mode == "or" else " AND "
        where.append("(" + joiner.join(pieces) + ")")
    if date_from:
        where.append("decided_at >= %(df)s"); params["df"] = date_from
    if date_to:
        where.append("decided_at <= %(dt)s"); params["dt"] = date_to
    if duty_type:
        where.append("duty_type ILIKE %(du)s"); params["du"] = f"%{duty_type}%"
    if person_rank:
        where.append("person_rank ILIKE %(pr)s"); params["pr"] = f"%{person_rank}%"
    if decision:
        where.append("decision ILIKE %(de)s"); params["de"] = f"%{decision}%"
    if review_type:
        where.append("review_type = %(rt)s"); params["rt"] = review_type
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    if query_vec is not None:
        params["vec"] = query_vec
        sim = "round((1 - (summary_embedding <=> %(vec)s::vector))::numeric, 4)"
        order = "summary_embedding <=> %(vec)s::vector"
    else:
        sim, order = "NULL", "decided_at DESC NULLS LAST, case_id DESC"
    sql = f"""
    SELECT case_id, review_type, exam_category, kcd_codes, decision, decided_at,
           duty_type, person_rank, summary, {sim} AS similarity
    FROM cases {where_sql} ORDER BY {order} LIMIT %(n)s
    """
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
