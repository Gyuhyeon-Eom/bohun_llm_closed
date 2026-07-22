# -*- coding: utf-8 -*-
"""그래프 RAG: 질의 → 엔티티 추출 → 지식그래프 다중홉 확장 → 근거 사실 라인.

벡터 하이브리드 검색(core/retrieval.py)이 못 잡는 관계형 질의를 결정적으로 보강한다:
  "정기조 상이등급은?"            → person→HAS_DOC→scan_doc→ASSIGNS_GRADE→grade
  "허혈성심장질환 대상자 몇 명?"   → disease←MENTIONS←scan_doc←HAS_DOC←person (집계)
  "당뇨병 판정에 필요한 서류는?"   → disease→MATCHES_RULE→jr_disease→JUDGED_BY→jr_axis→REQUIRES→jr_doc

LLM 미사용 — 그래프 자체가 출처이며, 사실 라인마다 근거(문서·안건번호)가 붙는다.
그래프 미적재 환경에서는 빈 결과를 돌려 챗봇이 벡터 검색만으로 동작한다 (안전 폴백).
온톨로지 정의: db/ONTOLOGY.md
"""
import re

import psycopg
from config.settings import PG_DSN

# \b는 한글도 \w라 "M21.27은"에서 경계가 안 잡힌다 — lookaround로 영숫자 경계만 검사
_RE_KCD = re.compile(r"(?<![A-Za-z0-9])[A-Z]\d{2}(?:\.\d{1,2})?(?!\d)")
_RE_GRADE = re.compile(r"\d급\s?\d?\s?항?(?:\s?\d{4}\s?호)?")


def extract_entities(query: str) -> dict:
    """질의에서 그래프 노드에 실재하는 엔티티만 추출 (DB 대조 — 환각 없음)."""
    ents = {"persons": [], "diseases": [], "kcds": [], "grades": []}
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        # 인명: person 노드 name과 대조 (질의 내 2~4자 한글 토큰)
        tokens = set(re.findall(r"[가-힣]{2,4}", query))
        if tokens:
            cur.execute("SELECT DISTINCT name FROM kg_nodes WHERE ntype='person'"
                        " AND name = ANY(%s)", (list(tokens),))
            ents["persons"] = [r[0] for r in cur.fetchall()]
        # 질환: disease 노드 key가 질의에 부분 포함되는지 (짧은 쪽 포함 매칭)
        cur.execute("SELECT key FROM kg_nodes WHERE ntype='disease'")
        ents["diseases"] = [k for (k,) in cur.fetchall() if k in query]
        # KCD 코드·등급 표기: 정규식 (그래프 실재 여부는 확장 단계에서 자연 필터)
        ents["kcds"] = _RE_KCD.findall(query)
        ents["grades"] = [re.sub(r"\s", "", g) for g in _RE_GRADE.findall(query)]
    return ents


def _rows(cur, sql, params):
    cur.execute(sql, params)
    return cur.fetchall()


def _person_facts(cur, names: list[str]) -> list[str]:
    """person 1홉: 문서·질환·등급·안건을 인물 단위로 요약."""
    facts = []
    for nm in names:
        rows = _rows(cur, """
            SELECT sd.key, sd.meta->>'doc_kind',
                   array_remove(array_agg(DISTINCT d.key), NULL),
                   array_remove(array_agg(DISTINCT g.key), NULL),
                   array_remove(array_agg(DISTINCT k.key), NULL)
            FROM kg_nodes p
            JOIN kg_edges e1 ON e1.src=p.node_id AND e1.etype='HAS_DOC'
            JOIN kg_nodes sd ON sd.node_id=e1.dst
            LEFT JOIN kg_edges e2 ON e2.src=sd.node_id AND e2.etype='MENTIONS'
            LEFT JOIN kg_nodes d ON d.node_id=e2.dst
            LEFT JOIN kg_edges e3 ON e3.src=sd.node_id AND e3.etype='ASSIGNS_GRADE'
            LEFT JOIN kg_nodes g ON g.node_id=e3.dst
            LEFT JOIN kg_edges e5 ON e5.src=sd.node_id AND e5.etype='MENTIONS_KCD'
            LEFT JOIN kg_nodes k ON k.node_id=e5.dst
            WHERE p.ntype='person' AND p.name=%s
            GROUP BY sd.key, sd.meta->>'doc_kind'""", (nm,))
        for sd_key, kind, dis, grd, kcd in rows:
            seg = f"{nm} — 문서[{kind or '스캔'}#{sd_key}]"
            if dis:
                seg += f" 질환: {', '.join(dis)}"
            if kcd:
                seg += f" (KCD {', '.join(kcd)})"
            if grd:
                seg += f" · 기재 등급: {', '.join(grd)}"
            facts.append(seg)
        for ano, injury, eg in _rows(cur, """
            SELECT ga.name, ga.meta->>'injury', ga.meta->>'exam_grade'
            FROM kg_nodes p
            JOIN kg_edges e ON e.src=p.node_id AND e.etype='HAS_AGENDA'
            JOIN kg_nodes ga ON ga.node_id=e.dst
            WHERE p.ntype='person' AND p.name=%s""", (nm,)):
            facts.append(f"{nm} — 상이등급 안건 {ano}: {injury or ''}"
                         + (f" / 신검등급 {eg}" if eg else ""))
    return facts


def _disease_facts(cur, diseases: list[str]) -> list[str]:
    """disease 1~2홉: 대상자 집계 + KCD + 판단기준룰(필요서류) 진입."""
    facts = []
    for d in diseases:
        rows = _rows(cur, """
            SELECT array_agg(DISTINCT p.name), count(DISTINCT p.node_id),
                   array_remove(array_agg(DISTINCT k.key), NULL)
            FROM kg_nodes dn
            JOIN kg_edges e1 ON e1.dst=dn.node_id AND e1.etype='MENTIONS'
            JOIN kg_nodes sd ON sd.node_id=e1.src
            JOIN kg_edges e2 ON e2.dst=sd.node_id AND e2.etype='HAS_DOC'
            JOIN kg_nodes p ON p.node_id=e2.src
            LEFT JOIN kg_edges e3 ON e3.src=dn.node_id AND e3.etype='CODED_AS'
            LEFT JOIN kg_nodes k ON k.node_id=e3.dst
            WHERE dn.ntype='disease' AND dn.key=%s""", (d,))
        for names, n, kcds in rows:
            if n:
                seg = f"{d} — 실데이터 대상자 {n}명: {', '.join(sorted(names))}"
                if kcds:
                    seg += f" (KCD {', '.join(kcds)})"
                facts.append(seg)
        # 판단기준룰 레이어 진입 (disease→MATCHES_RULE→jr_disease→JUDGED_BY→REQUIRES)
        for sub, axis, cond, basis, docs in _rows(cur, """
            SELECT s.name, a.name, a.meta->>'condition', a.meta->>'basis',
                   array_remove(array_agg(doc.key), NULL)
            FROM kg_nodes dn
            JOIN kg_edges m ON m.src=dn.node_id AND m.etype='MATCHES_RULE'
            JOIN kg_nodes jd ON jd.node_id=m.dst
            JOIN kg_edges e1 ON e1.dst=jd.node_id AND e1.etype='HAS_DISEASE'
            JOIN kg_nodes s ON s.node_id=e1.src
            JOIN kg_edges e2 ON e2.src=jd.node_id AND e2.etype='JUDGED_BY'
            JOIN kg_nodes a ON a.node_id=e2.dst
            LEFT JOIN kg_edges e3 ON e3.src=a.node_id AND e3.etype='REQUIRES'
            LEFT JOIN kg_nodes doc ON doc.node_id=e3.dst
            WHERE dn.ntype='disease' AND dn.key=%s
            GROUP BY 1,2,3,4""", (d,)):
            seg = f"{d} 판단기준 〔{sub}·{axis}〕 {cond or ''}"
            if docs:
                seg += f" (필요서류: {', '.join(docs)})"
            if basis:
                seg += f" [근거: {basis}]"
            facts.append(seg)
    return facts


def _kcd_facts(cur, kcds: list[str]) -> list[str]:
    facts = []
    for code in kcds:
        for dis, persons in _rows(cur, """
            SELECT array_remove(array_agg(DISTINCT d.key), NULL),
                   array_remove(array_agg(DISTINCT p.name), NULL)
            FROM kg_nodes k
            LEFT JOIN kg_edges c ON c.dst=k.node_id AND c.etype='CODED_AS'
            LEFT JOIN kg_nodes d ON d.node_id=c.src
            LEFT JOIN kg_edges mk ON mk.dst=k.node_id AND mk.etype='MENTIONS_KCD'
            LEFT JOIN kg_nodes sd ON sd.node_id=mk.src
            LEFT JOIN kg_edges hd ON hd.dst=sd.node_id AND hd.etype='HAS_DOC'
            LEFT JOIN kg_nodes p ON p.node_id=hd.src
            WHERE k.ntype='kcd' AND k.key=%s""", (code,)):
            if dis or persons:
                facts.append(f"KCD {code} — 질환: {', '.join(dis or ['?'])}"
                             + (f" / 대상자: {', '.join(persons)}" if persons else ""))
    return facts


def graph_facts(query: str, n: int = 10) -> dict:
    """질의 관련 그래프 사실 라인. {'facts': [...], 'entities': {...}}
    그래프·DB 미적재 시 빈 결과 (챗봇은 벡터 검색만으로 동작)."""
    try:
        ents = extract_entities(query)
        facts = []
        with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
            if ents["persons"]:
                facts += _person_facts(cur, ents["persons"])
            if ents["diseases"]:
                facts += _disease_facts(cur, ents["diseases"])
            if ents["kcds"]:
                facts += _kcd_facts(cur, ents["kcds"])
        return {"facts": facts[:n], "entities": ents}
    except Exception:
        return {"facts": [], "entities": {}}
