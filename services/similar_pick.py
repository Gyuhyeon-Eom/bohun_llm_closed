# -*- coding: utf-8 -*-
"""유사사례 커스터마이징 — 260721 회의 ③ 반영.

담당자·위원이 제시된 유사사례 중 부적절한 것을 제외(exclude)하거나 직접 찾은
사례를 추가·고정(pin, 가중치)할 수 있고, 그 선별 결과가 AI 사전판단과
판단문 생성에 그대로 반영된다. 사유(note)를 남겨 감사 추적 가능(HITL).

scope='case'  요건심사: (app_id, dis_id) 단위, cases.case_id 참조
scope='grade' 등급심사: (ga_id) 단위, grade_case.gc_id 참조
"""
import psycopg
from psycopg.rows import dict_row

from config.settings import PG_DSN


def set_pick(scope, case_id, kind, app_id=None, dis_id=None, ga_id=None,
             weight=1.0, note=None) -> dict:
    """kind=exclude|pin 저장(동일 키 대체), kind=clear 는 해제."""
    if scope not in ("case", "grade") or kind not in ("exclude", "pin", "clear"):
        return {"error": "scope=case|grade, kind=exclude|pin|clear"}
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("""DELETE FROM similar_pick WHERE scope=%s AND case_id=%s
                       AND COALESCE(app_id,0)=COALESCE(%s,0) AND COALESCE(dis_id,0)=COALESCE(%s,0)
                       AND COALESCE(ga_id,0)=COALESCE(%s,0)""",
                    (scope, case_id, app_id, dis_id, ga_id))
        if kind != "clear":
            cur.execute("""INSERT INTO similar_pick(scope, app_id, dis_id, ga_id, case_id,
                           kind, weight, note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (scope, app_id, dis_id, ga_id, case_id, kind, weight, note))
        conn.commit()
    return {"ok": True, "kind": kind, "case_id": case_id}


def get_picks(scope, app_id=None, dis_id=None, ga_id=None) -> list[dict]:
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("""SELECT * FROM similar_pick WHERE scope=%s
                       AND COALESCE(app_id,0)=COALESCE(%s,0) AND COALESCE(dis_id,0)=COALESCE(%s,0)
                       AND COALESCE(ga_id,0)=COALESCE(%s,0) ORDER BY sp_id""",
                    (scope, app_id, dis_id, ga_id))
        return cur.fetchall()


def apply_picks(items: list[dict], picks: list[dict], id_key: str,
                fetch_pinned=None) -> list[dict]:
    """검색 결과에 선별 반영: 제외 제거 → 미포함 고정건 fetch 추가 → 고정(가중치순) 우선 정렬.
    각 항목에 pick(exclude 항목은 반환 전 제거되므로 'pin'|None)과 pick_note 표기."""
    ex = {p["case_id"] for p in picks if p["kind"] == "exclude"}
    pin = {p["case_id"]: p for p in picks if p["kind"] == "pin"}
    out = [dict(x) for x in items if x.get(id_key) not in ex]
    have = {x.get(id_key) for x in out}
    missing = [cid for cid in pin if cid not in have]
    if missing and fetch_pinned:
        for row in fetch_pinned(missing) or []:
            out.append(dict(row))
    for x in out:
        p = pin.get(x.get(id_key))
        x["pick"] = "pin" if p else None
        x["pick_weight"] = p["weight"] if p else None
        x["pick_note"] = p["note"] if p else None
    out.sort(key=lambda x: (0 if x["pick"] else 1,
                            -(x["pick_weight"] or 0),
                            -(float(x.get("similarity") or 0))))
    return out


def fetch_cases(case_ids: list[int]) -> list[dict]:
    """요건심사 cases 풀에서 고정 추가건 조회 (similarity 없음 → 담당자 추가 표기)."""
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("""SELECT case_id, review_type, exam_category, kcd_codes, decision,
                              decided_at::text AS decided_at, summary, NULL::numeric AS similarity,
                              kcd_codes AS matched_codes
                       FROM cases WHERE case_id = ANY(%s)""", (case_ids,))
        return cur.fetchall()


def fetch_grade_cases(gc_ids: list[int]) -> list[dict]:
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("""SELECT gc_id, recv_no, meeting_date::text AS meeting_date, disease_name,
                              body_part, grade, order_text, opinion_text, NULL::numeric AS similarity
                       FROM grade_case WHERE gc_id = ANY(%s)""", (gc_ids,))
        return cur.fetchall()


def search_cases(q: str, n: int = 10) -> list[dict]:
    """위원이 직접 추가할 사례 검색 (요약문 부분일치 — 인정/비인정 구분 포함)."""
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("""SELECT case_id, review_type, kcd_codes, decision,
                              decided_at::text AS decided_at, summary
                       FROM cases WHERE summary ILIKE %s OR %s = ANY(kcd_codes)
                       ORDER BY decided_at DESC LIMIT %s""",
                    (f"%{q}%", q.upper(), n))
        return cur.fetchall()
