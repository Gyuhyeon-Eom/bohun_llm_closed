# -*- coding: utf-8 -*-
"""실데이터 인스턴스 레이어 → 지식그래프 확장. 멱등 (인스턴스 ntype만 재생성).

스키마 레이어(build_graph.py: 심의체계·조문·KCD·사례, build_rule_graph.py: 판단기준룰)
위에 스캔 실데이터를 인스턴스로 연결한다. 그래프 RAG(core/graph_rag.py)의 원천.

노드:  person(대상자) · scan_doc(스캔묶음) · disease(질환) · grade(등급) ·
       hospital(발행기관) · grade_agenda(안건)
엣지:  person-HAS_DOC->scan_doc          scan_doc-MENTIONS->disease
       scan_doc-ASSIGNS_GRADE->grade     scan_doc-ISSUED_BY->hospital
       scan_doc-CONVERTED_TO->grade_agenda   person-HAS_AGENDA->grade_agenda
       disease-CODED_AS->kcd             disease-MATCHES_RULE->jr_disease(판단기준 연결)

실행 순서 (build_graph.py가 kg 전체를 TRUNCATE 하므로 반드시 마지막):
  python3 db/build_graph.py && python3 scripts/build_rule_graph.py && python3 db/build_instance_graph.py
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import psycopg
from config.settings import PG_DSN

INSTANCE_NTYPES = ("person", "scan_doc", "disease", "grade", "hospital", "grade_agenda")

# 질병명 자리에 들어오는 라벨·잡음 (OCR 표 구조 붕괴 산출물)
_NOT_DISEASE = {"임상적추정", "병상일지", "신청사유", "의무기록", "소견서", "진단서",
                "수술기록", "외래기록", "입퇴원요약", "영상검사결과", "기능검사결과"}


def clean_diseases(raw: str | None) -> list[str]:
    """'방광암 *조기검진의뢰 to' → ['방광암'], '당뇨병,허혈성심장질환' → 두 건 분리.
    '폐암' 같은 2자 질환은 허용, '7초검진'류 숫자 시작·검사 계열 라벨은 거부."""
    if not raw:
        return []
    out = []
    for part in re.split(r"[,·/]", raw.split("*")[0]):
        v = part.strip()
        if (len(v) >= 2 and re.fullmatch(r"[가-힣A-Za-z0-9() ]+", v)
                and not v[0].isdigit()
                and re.search(r"[가-힣]{2,}", v)
                and v not in _NOT_DISEASE
                and not re.search(r"검진|검사|판독|소견|기록|서류|구분|성명|번호", v)):
            out.append(v)
    return out


def node(cur, ntype, key, name=None, meta=None):
    cur.execute(
        "INSERT INTO kg_nodes(ntype,key,name,meta) VALUES (%s,%s,%s,%s)"
        " ON CONFLICT (ntype,key) DO UPDATE SET name=EXCLUDED.name, meta=EXCLUDED.meta"
        " RETURNING node_id", (ntype, key, name or key, psycopg.types.json.Jsonb(meta or {})))
    return cur.fetchone()[0]


def edge(cur, src, dst, etype, meta=None):
    if src and dst:
        cur.execute("INSERT INTO kg_edges(src,dst,etype,meta) VALUES (%s,%s,%s,%s)",
                    (src, dst, etype, psycopg.types.json.Jsonb(meta or {})))


def main():
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        # 멱등: 인스턴스 노드만 삭제 (엣지는 CASCADE) — 스키마 레이어는 보존
        cur.execute("DELETE FROM kg_nodes WHERE ntype = ANY(%s)", (list(INSTANCE_NTYPES),))

        # 기존 스키마 레이어 참조 인덱스
        cur.execute("SELECT node_id, key FROM kg_nodes WHERE ntype='kcd'")
        kcd_ix = dict((k, i) for i, k in cur.fetchall())
        cur.execute("SELECT node_id, key FROM kg_nodes WHERE ntype='jr_disease'")
        jr_ix = list(cur.fetchall())  # (node_id, regex 패턴)

        cur.execute("""SELECT sd_id, person, reg_no, hospital, doc_kind, file_name,
                              exams, ga_id, app_id, is_real FROM scan_doc WHERE is_real""")
        scans = cur.fetchall()
        n_dis = n_edge = 0
        persons, diseases, grades, hospitals = {}, {}, {}, {}

        for sd_id, person, reg_no, hospital, doc_kind, fname, exams, ga_id, app_id, _ in scans:
            blocks = exams if isinstance(exams, list) else json.loads(exams or "[]")
            sd_n = node(cur, "scan_doc", str(sd_id), f"{person or '미상'} {doc_kind or ''}".strip(),
                        {"file_name": fname, "doc_kind": doc_kind, "n_blocks": len(blocks),
                         "ga_id": ga_id, "app_id": app_id})
            if person:
                pkey = f"{person}:{reg_no or ''}"
                if pkey not in persons:
                    persons[pkey] = node(cur, "person", pkey, person,
                                         {"reg_no": reg_no, "masked": True})
                edge(cur, persons[pkey], sd_n, "HAS_DOC"); n_edge += 1
            if hospital:
                if hospital not in hospitals:
                    hospitals[hospital] = node(cur, "hospital", hospital)
                edge(cur, sd_n, hospitals[hospital], "ISSUED_BY"); n_edge += 1

            sd_diseases = []   # 이 문서에서 언급된 질환 노드 (문서 단위 KCD 연결용)
            sd_kcds = []
            for b in blocks:
                f, nm = b.get("fields") or {}, b.get("norm") or {}
                for d in clean_diseases(nm.get("disease") or f.get("disease")):
                    if d not in diseases:
                        dn = diseases[d] = node(cur, "disease", d)
                        # 판단기준룰 연결 (jr_disease.key = 정규식 패턴)
                        for jr_id, pat in jr_ix:
                            try:
                                if re.search(pat, d):
                                    edge(cur, dn, jr_id, "MATCHES_RULE")
                            except re.error:
                                pass
                    edge(cur, sd_n, diseases[d], "MENTIONS",
                         {"doc": b.get("doc"), "line": b.get("line")}); n_dis += 1
                    sd_diseases.append(diseases[d])
                kcd = f.get("kcd")
                if kcd:
                    if kcd not in kcd_ix:
                        kcd_ix[kcd] = node(cur, "kcd", kcd)
                    sd_kcds.append((kcd_ix[kcd], b.get("doc")))
                g = nm.get("grade") or f.get("grade")
                if g:
                    g = re.sub(r"\s+", "", g)
                    if g not in grades:
                        grades[g] = node(cur, "grade", g)
                    edge(cur, sd_n, grades[g], "ASSIGNS_GRADE",
                         {"doc": b.get("doc"), "line": b.get("line")}); n_edge += 1

            # 문서 단위 KCD 연결: KCD는 진단서, 질병명은 소견서처럼 서로 다른 블록에
            # 나오는 경우가 대부분 — 같은 묶음이면 동일 대상자의 상병으로 보고 연결
            for kn, doc in sd_kcds:
                edge(cur, sd_n, kn, "MENTIONS_KCD", {"doc": doc}); n_edge += 1
                for dn in dict.fromkeys(sd_diseases):
                    edge(cur, dn, kn, "CODED_AS", {"sd_id": sd_id})

        # 안건 노드 + 연결
        cur.execute("""SELECT ga_id, agenda_no, applicant, injury, exam_grade, is_real
                       FROM grade_agenda""")
        for ga_id, ano, applicant, injury, exam_grade, is_real in cur.fetchall():
            ga_n = node(cur, "grade_agenda", str(ga_id), ano,
                        {"injury": injury, "exam_grade": exam_grade, "is_real": is_real})
            cur.execute("SELECT sd_id, person, reg_no FROM scan_doc WHERE ga_id=%s", (ga_id,))
            for sd_id, person, reg_no in cur.fetchall():
                cur.execute("SELECT node_id FROM kg_nodes WHERE ntype='scan_doc' AND key=%s",
                            (str(sd_id),))
                r = cur.fetchone()
                if r:
                    edge(cur, r[0], ga_n, "CONVERTED_TO")
                pk = f"{person}:{reg_no or ''}"
                if pk in persons:
                    edge(cur, persons[pk], ga_n, "HAS_AGENDA")

        conn.commit()
        cur.execute("SELECT ntype, count(*) FROM kg_nodes WHERE ntype=ANY(%s) GROUP BY 1",
                    (list(INSTANCE_NTYPES),))
        print("인스턴스 레이어:", dict(cur.fetchall()), f"/ MENTIONS {n_dis}건")


if __name__ == "__main__":
    main()
