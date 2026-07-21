# -*- coding: utf-8 -*-
"""판단기준 룰 그래프 적재 — 정형화틀 v2.4 온톨로지 확장 (graph-lite).

judgment_rule(분과·질환·판단축·조건·필요서류·근거)을 kg_nodes/kg_edges에 파생 적재해
"질환 → 판단축 → 필요서류/근거" 멀티홉 탐색을 가능하게 한다.
챗봇·화면은 core/graph.rule_facts()로 결정적 그래프 근거를 조회한다.

노드: jr_sub(분과) / jr_disease(질환 패턴) / jr_axis(판단축) / jr_doc(필요서류)
엣지: HAS_DISEASE(분과→질환) / JUDGED_BY(질환→축, meta=조건) / REQUIRES(축→서류)

기존 그래프(build_graph.py 산출)와 ntype 접두(jr_)로 분리 — 재실행 시 jr_* 만 재생성.
실행: python3 scripts/seed_judgment_rules.py 후 python3 scripts/build_rule_graph.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    import psycopg
    from config.settings import PG_DSN

    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT jr_id, subcommittee, disease_pattern, axis, condition,"
                    " check_kind, required_docs, basis FROM judgment_rule ORDER BY jr_id")
        rules = cur.fetchall()
        if not rules:
            print("judgment_rule 비어있음 — scripts/seed_judgment_rules.py 먼저 실행")
            return

        cur.execute("DELETE FROM kg_nodes WHERE ntype LIKE 'jr_%'")  # 엣지는 CASCADE

        def node(ntype, key, name=None, meta=None):
            import json
            cur.execute("""INSERT INTO kg_nodes(ntype, key, name, meta) VALUES (%s,%s,%s,%s)
                           ON CONFLICT (ntype, key) DO UPDATE SET name=EXCLUDED.name
                           RETURNING node_id""",
                        (ntype, key, name or key, json.dumps(meta or {}, ensure_ascii=False)))
            return cur.fetchone()[0]

        def edge(src, dst, etype, meta=None):
            import json
            cur.execute("INSERT INTO kg_edges(src, dst, etype, meta) VALUES (%s,%s,%s,%s)",
                        (src, dst, etype, json.dumps(meta or {}, ensure_ascii=False)))

        n_e = 0
        for jr_id, sub, pattern, axis, cond, kind, docs, basis in rules:
            sub_n = node("jr_sub", sub, f"제{sub}분과")
            dis_n = node("jr_disease", pattern, pattern.replace("|", "·"))
            ax_n = node("jr_axis", f"{sub}:{axis}", axis,
                        {"condition": cond, "check_kind": kind, "basis": basis, "jr_id": jr_id})
            edge(sub_n, dis_n, "HAS_DISEASE")
            edge(dis_n, ax_n, "JUDGED_BY", {"condition": cond})
            n_e += 2
            for dc in (docs or []):
                doc_n = node("jr_doc", dc)
                edge(ax_n, doc_n, "REQUIRES")
                n_e += 1
        conn.commit()
        cur.execute("SELECT ntype, count(*) FROM kg_nodes WHERE ntype LIKE 'jr_%' GROUP BY 1 ORDER BY 1")
        print("노드:", dict(cur.fetchall()), f"/ 엣지 {n_e}개 (룰 {len(rules)}건)")


if __name__ == "__main__":
    main()
