# -*- coding: utf-8 -*-
"""그래프 RAG 회귀 테스트.

1부: 순수 로직 (DB 불필요 — 어디서나 실행)
2부: 그래프 통합 (DB + 인스턴스 그래프 적재 환경에서만 — 미적재 시 SKIP)
실행: python3 tests/test_graph_rag.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = FAIL = SKIP = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS {name}")
    else:
        FAIL += 1
        print(f"FAIL {name}")


def skip(name, why):
    global SKIP
    SKIP += 1
    print(f"SKIP {name} — {why}")


# ── 1부: 순수 로직 ──────────────────────────────────────────
from db.build_instance_graph import clean_diseases

check("질병명 정리 — 잡음 접미 제거", clean_diseases("방광암 *조기검진의뢰 to") == ["방광암"])
check("질병명 정리 — 복수 질환 분리",
      clean_diseases("당뇨병,허혈성심장질환") == ["당뇨병", "허혈성심장질환"])
check("질병명 정리 — 라벨 거부", clean_diseases("임상적추정") == [])
check("질병명 정리 — 영문 잡음 거부", clean_diseases("if") == [])
check("질병명 정리 — None 안전", clean_diseases(None) == [])
check("질병명 정리 — 2자 질환 허용", clean_diseases("폐암 *조기검진의뢰") == ["폐암"])
check("질병명 정리 — 숫자 시작·검사 라벨 거부", clean_diseases("7초검진") == [])

from core.graph_rag import _RE_KCD, _RE_GRADE

check("KCD 정규식", _RE_KCD.findall("S26 코드와 M21.27은?") == ["S26", "M21.27"])
check("등급 정규식", [g.replace(" ", "") for g in _RE_GRADE.findall("6급 2항 5108호 판정")] == ["6급2항5108호"])

# ── 2부: 그래프 통합 (적재 환경에서만) ───────────────────────
try:
    import psycopg
    from config.settings import PG_DSN
    with psycopg.connect(PG_DSN, connect_timeout=3) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM kg_nodes WHERE ntype='person'")
        n_person = cur.fetchone()[0]
except Exception as e:
    n_person = None
    skip("그래프 통합 (2부 전체)", f"DB 접속 불가: {type(e).__name__}")

if n_person is not None and n_person == 0:
    skip("그래프 통합 (2부 전체)", "인스턴스 그래프 미적재 — db/build_instance_graph.py 실행 후 재시도")
elif n_person:
    from core.graph_rag import extract_entities, graph_facts

    # 실재 인물 1명을 그래프에서 뽑아 질의 구성 (데이터 독립적)
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT name FROM kg_nodes WHERE ntype='person' LIMIT 1")
        someone = cur.fetchone()[0]
        cur.execute("SELECT key FROM kg_nodes WHERE ntype='disease' LIMIT 1")
        row = cur.fetchone()
        some_disease = row[0] if row else None

    ents = extract_entities(f"{someone} 대상자의 등급은?")
    check("엔티티 추출 — 실재 인명 인식", someone in ents["persons"])
    check("엔티티 추출 — 미실재 인명 배제", "홍길동" not in ents["persons"])

    r = graph_facts(f"{someone} 문서와 안건 알려줘")
    check("인명 질의 — 사실 라인 생성", len(r["facts"]) >= 1)
    check("인명 질의 — 인명 포함", any(someone in f for f in r["facts"]))

    if some_disease:
        r2 = graph_facts(f"{some_disease} 대상자는 몇 명?")
        check("질환 질의 — 집계 라인", any("대상자" in f and "명" in f for f in r2["facts"]))

    check("무관 질의 — 빈 결과", graph_facts("오늘 날씨 어때?")["facts"] == [])

print(f"\n결과: PASS {PASS} / FAIL {FAIL} / SKIP {SKIP}")
sys.exit(1 if FAIL else 0)
