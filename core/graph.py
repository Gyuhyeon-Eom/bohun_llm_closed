"""지식그래프 탐색 헬퍼. 검토서(⑤)가 근거 체인을 결정적으로 수집할 때 사용."""
import psycopg
from config.settings import PG_DSN


def applied_clauses(review_content: str, target_cond: str = "") -> list[dict]:
    """심의내용 -APPLIES-> 조문. 주문(판단내용·결과)과 조문 메타(표준문안 코드)를 한 번에."""
    sql = """
    SELECT e.meta->>'pair_order' AS pair_order, c.key AS clause, e.meta->>'result' AS result,
           c.meta->>'code' AS std_code, (c.meta->>'injury_related')::boolean AS injury_related,
           e.meta->>'rule_no' AS rule_no
    FROM kg_edges e
    JOIN kg_nodes s ON s.node_id = e.src AND s.ntype='review_content' AND s.key=%s
    JOIN kg_nodes c ON c.node_id = e.dst
    WHERE e.etype='APPLIES' AND COALESCE(e.meta->>'target_cond','')=%s
    ORDER BY (e.meta->>'rule_no')::numeric, (e.meta->>'pair_order')::int
    """
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute(sql, (review_content, target_cond))
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def cases_by_kcd(kcd_codes: list[str], n: int = 5) -> list[dict]:
    """KCD 노드 역방향 탐색: 같은 상이처(KCD)를 가진 과거 사례 + 판정 (겹치는 코드 수 순)."""
    if not kcd_codes:
        return []
    sql = """
    SELECT cs.key::bigint AS case_id, cs.meta->>'decision' AS decision,
           count(*) AS shared_kcd, array_agg(k.key) AS matched_codes
    FROM kg_nodes k
    JOIN kg_edges e ON e.dst = k.node_id AND e.etype='HAS_KCD'
    JOIN kg_nodes cs ON cs.node_id = e.src AND cs.ntype='case'
    WHERE k.ntype='kcd' AND k.key = ANY(%s)
    GROUP BY cs.key, cs.meta->>'decision'
    ORDER BY shared_kcd DESC LIMIT %s
    """
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute(sql, (kcd_codes, n))
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def rule_conflicts() -> list[dict]:
    """같은 (심의내용·판단대상·조문)에 서로 다른 결과가 걸린 규칙 충돌 검출 (품질 점검용)."""
    sql = """
    SELECT s.key AS review_content, e.meta->>'target_cond' AS target_cond,
           c.key AS clause, array_agg(DISTINCT e.meta->>'result') AS results
    FROM kg_edges e
    JOIN kg_nodes s ON s.node_id=e.src JOIN kg_nodes c ON c.node_id=e.dst
    WHERE e.etype='APPLIES'
    GROUP BY 1,2,3 HAVING count(DISTINCT e.meta->>'result') > 1
    """
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute(sql)
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
