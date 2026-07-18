"""하이브리드 검색: dense(pgvector) + sparse(tsvector) -> RRF 결합.

조문번호·KCD 정확 매칭은 sparse, 의미 유사는 dense가 담당.
doc_type 필터는 같은 SQL WHERE로 처리 (단일 DB의 이점).
"""
import re
import psycopg
from config.settings import PG_DSN, TOP_K, RRF_K, RRF_DENSE_WEIGHT, RRF_SPARSE_WEIGHT


def _or_tsquery(query: str) -> str:
    """질의 토큰의 OR 결합. plainto_tsquery의 AND 의미는 한국어(조사 변이)와
    저품질 OCR 텍스트에서 재현율을 0에 가깝게 만든다 - 목데이터 실측으로 확인된 버그 수정.
    랭킹은 ts_rank가 일치 토큰 수 기준으로 처리."""
    tokens = re.findall(r"[0-9A-Za-z가-힣\-]+", query)
    return " | ".join(dict.fromkeys(tokens)) or "___none___"


def hybrid_search(query: str, query_vec: list[float],
                  top_k: int = TOP_K, doc_type: str | None = None,
                  use_dense: bool = True) -> list[dict]:
    """use_dense=False면 sparse(어휘 일치)만 사용.
    대역(hash) 임베더 환경이나 sparse 단독 품질 튜닝 시 사용."""
    dt_where = "WHERE d.doc_type = %(dt)s" if doc_type else ""
    sql = f"""
    WITH pool AS (
      SELECT c.chunk_id, c.embedding, c.content_tsv
      FROM chunks c JOIN documents d USING (doc_id) {dt_where}
    ), dense AS (
      SELECT chunk_id, row_number() OVER (ORDER BY embedding <=> %(vec)s::vector, chunk_id) AS r
      FROM pool ORDER BY embedding <=> %(vec)s::vector LIMIT 50
    ), sparse AS (
      SELECT chunk_id, row_number() OVER (
        ORDER BY ts_rank(content_tsv, to_tsquery('simple', %(q)s)) DESC, chunk_id) AS r
      FROM pool WHERE content_tsv @@ to_tsquery('simple', %(q)s) LIMIT 50
    ), fused AS (
      SELECT chunk_id, sum(w/(%(k)s + r)) AS score
      FROM (SELECT chunk_id, r, %(wd)s::float AS w FROM dense WHERE %(dense)s
            UNION ALL SELECT chunk_id, r, %(ws)s::float FROM sparse) u
      GROUP BY chunk_id ORDER BY score DESC, chunk_id LIMIT %(top)s
    )
    SELECT c.chunk_id, c.content, c.block_type, c.page_no, d.source_path, f.score
    FROM fused f JOIN chunks c USING (chunk_id) JOIN documents d USING (doc_id)
    ORDER BY f.score DESC
    """
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute(sql, {"vec": query_vec, "q": _or_tsquery(query), "k": RRF_K, "top": top_k,
                          "dt": doc_type, "dense": use_dense,
                          "wd": RRF_DENSE_WEIGHT, "ws": RRF_SPARSE_WEIGHT})
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
