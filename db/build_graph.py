"""시드 테이블 -> 지식그래프 변환. 멱등 (전체 재생성).

노드: 심의유형·심의내용·의제·조문(표준문안)·KCD·사례
엣지: 유형-HAS_CONTENT->내용, 내용-HAS_AGENDA->의제,
      내용-APPLIES{target_cond,result,순서}->조문 (주문안 규칙 296건),
      사례-OF_TYPE->유형, 사례-HAS_KCD->KCD
KCD 노드는 21만 전체가 아니라 '사례·규칙에 등장하는 코드만' 생성 (그래프 비대 방지).
TODO(확인): 법령 조문 간 참조(예우법<->시행령<->시행규칙 별표) 엣지는
  법령 PDF 디지털화 후 조문 파싱으로 추가 - 현재는 코드 테이블 기반 관계만.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import psycopg
from config.settings import PG_DSN


def node(cur, ntype, key, name=None, meta=None):
    cur.execute(
        "INSERT INTO kg_nodes(ntype,key,name,meta) VALUES (%s,%s,%s,%s)"
        " ON CONFLICT (ntype,key) DO UPDATE SET name=EXCLUDED.name, meta=EXCLUDED.meta"
        " RETURNING node_id", (ntype, key, name or key, psycopg.types.json.Jsonb(meta or {})))
    return cur.fetchone()[0]


def edge(cur, src, dst, etype, meta=None):
    cur.execute("INSERT INTO kg_edges(src,dst,etype,meta) VALUES (%s,%s,%s,%s)",
                (src, dst, etype, psycopg.types.json.Jsonb(meta or {})))


def main():
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute(open(Path(__file__).parent / "graph_schema.sql").read())
        cur.execute("TRUNCATE kg_edges, kg_nodes RESTART IDENTITY CASCADE")

        # 1) 심의체계 계층
        cur.execute("SELECT name FROM review_type WHERE in_use")
        rt = {n: node(cur, "review_type", n) for (n,) in cur.fetchall()}
        cur.execute("SELECT review_type_name, content FROM review_content WHERE in_use")
        rc = {}
        for rtn, c in cur.fetchall():
            rc[c] = node(cur, "review_content", c)
            if rtn in rt:
                edge(cur, rt[rtn], rc[c], "HAS_CONTENT")
        cur.execute("SELECT review_type_name, content, agenda FROM agenda WHERE in_use")
        for rtn, c, ag in cur.fetchall():
            a = node(cur, "agenda", ag)
            edge(cur, rc.get(c) or rt.get(rtn) or a, a, "HAS_AGENDA")

        # 2) 조문 노드 (표준문안 = 조문 카탈로그)
        cur.execute("SELECT code, name, injury_related FROM standard_clause WHERE in_use")
        clause = {n: node(cur, "clause", n, meta={"code": c, "injury_related": i})
                  for c, n, i in cur.fetchall()}

        # 3) 주문안 규칙 -> APPLIES 엣지
        cur.execute("""SELECT review_content, COALESCE(target_cond,''), pair_order,
                              judge_item, result, rule_no
                       FROM auto_order_rule WHERE in_use""")
        n_rules = 0
        for c, tc, po, ji, res, rn in cur.fetchall():
            src = rc.get(c) or node(cur, "review_content", c)
            rc[c] = src
            dst = clause.get(ji) or node(cur, "clause", ji)  # 표준문안에 없는 조문도 노드화
            clause[ji] = dst
            edge(cur, src, dst, "APPLIES",
                 {"target_cond": tc, "result": res, "pair_order": po, "rule_no": rn})
            n_rules += 1

        # 4) 사례 -> 유형·KCD 연결 (KCD 노드는 등장분만 생성)
        cur.execute("SELECT case_id, review_type, kcd_codes, decision FROM cases")
        for cid, rtn, kcds, dec in cur.fetchall():
            cn = node(cur, "case", str(cid), meta={"decision": dec})
            if rtn in rt:
                edge(cur, cn, rt[rtn], "OF_TYPE")
            for code in (kcds or []):
                cur.execute("SELECT disease_name, kcd_name FROM kcd WHERE kcd_code=%s LIMIT 1", (code,))
                row = cur.fetchone()
                kn = node(cur, "kcd", code, name=(row[0] if row else code),
                          meta={"kcd_name": row[1]} if row else {})
                edge(cur, cn, kn, "HAS_KCD")

        cur.execute("SELECT count(*) FROM kg_nodes"); nn = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM kg_edges"); ne = cur.fetchone()[0]
        print(f"그래프 생성: 노드 {nn} / 엣지 {ne} (규칙 {n_rules})")


if __name__ == "__main__":
    main()
