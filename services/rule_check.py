# -*- coding: utf-8 -*-
"""분과 판단기준 자동대조 — 정형화틀 v2.4 룰(judgment_rule)을 사건 데이터와 결정적으로 대조.

원칙: 계산·대조는 코드가, 서술은 LLM이. 여기서는 LLM을 쓰지 않는다.
- doc  룰: 필요서류 키워드를 보유 자료 텍스트 풀(의무기록·공적서류·스캔 하위문서)과 대조
- auto 룰: 계산 가능한 조건 판정 (현재: MRI 촬영일-부상일 3개월 이내 = 급성/진구성 분기)
- manual 룰: 담당자 확인 항목으로 표시만

결과 status: ok(충족) / lack(자료 부족) / manual(담당자 확인) — HITL 전제, 자동 확정 없음.
"""
import re
from datetime import date

import psycopg
from psycopg.rows import dict_row

from config.settings import PG_DSN


def _parse_date(s):
    if not s:
        return None
    m = re.search(r"(\d{4})[.\-/년\s]{1,2}(\d{1,2})[.\-/월\s]{1,2}(\d{1,2})", str(s))
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.search(r"(\d{4})[.\-/](\d{1,2})", str(s))  # 연월만 (onset_ym '2019.03')
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), 15)
        except ValueError:
            return None
    return None


def _mri_check(dis, meds):
    """급성/진구성 분기: MRI 촬영일이 부상일(사실발생일/발병연월) 3개월 이내인지."""
    base = _parse_date(dis.get("fact_date")) or _parse_date(dis.get("onset_ym"))
    mri_dates = [_parse_date(m.get("rec_date")) for m in meds
                 if "MRI" in f"{m.get('imaging') or ''}{m.get('rec_type') or ''}".upper()]
    mri_dates = [d for d in mri_dates if d]
    if not base or not mri_dates:
        return "manual", "부상일 또는 MRI 촬영일 미확인 — 원문 대조 필요"
    days = min(abs((d - base).days) for d in mri_dates)
    if days <= 92:
        return "ok", f"MRI 촬영이 부상일 기준 {days}일 — 3개월 이내 (급성 축 충족)"
    return "lack", f"MRI 촬영이 부상일 기준 {days}일 경과 — 3개월 초과 (진구성 소견 여부 재판독 필요)"


# ── 신청상이 ↔ 판단문 일치 검사 (검토의견 35번·1분과: 왼쪽팔 신청 → 오른팔 의결 오기 검출) ──
# '좌골'(부위명)·'좌상'(挫傷) 같은 어휘가 좌측으로 오탐되지 않게 단독 '좌/우'는 쓰지 않는다.
_L = re.compile(r"왼쪽|왼측|왼|좌측")
_R = re.compile(r"오른쪽|오른측|오른|우측")
_B = re.compile(r"양측|양쪽|양하지|양상지|양무릎|양어깨")


def _sides(text: str) -> set[str]:
    """텍스트에서 좌/우/양측 표기를 추출 — 'L'·'R'·'B' 집합."""
    t = str(text or "")
    out = set()
    if _B.search(t):
        out.add("B")
    if _L.search(t):
        out.add("L")
    if _R.search(t):
        out.add("R")
    return out


def side_check(app_id: int) -> dict:
    """상이처별 신청 표기(명칭·부위측)와 판단문(conclusion) 좌우 표기 대조.
    결정적 검사 — LLM 미사용. 마지막 체크단계(확정 전) 화면에서 호출."""
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT dis_id, name, body_side FROM disability WHERE app_id=%s"
                    " ORDER BY dis_id", (app_id,))
        diss = cur.fetchall()
        cur.execute("SELECT dis_id, body_text, final_text FROM conclusion WHERE app_id=%s",
                    (app_id,))
        cons = {c["dis_id"]: c for c in cur.fetchall()}
    items, warns = [], 0
    for d in diss:
        applied = _sides(f"{d.get('name') or ''} {d.get('body_side') or ''}")
        con = cons.get(d["dis_id"]) or {}
        judged = _sides(f"{con.get('body_text') or ''} {con.get('final_text') or ''}")
        if not applied or "B" in applied:
            status, note = "ok", None                      # 좌우 구분 없는 상이처는 대상 외
        elif not judged:
            status, note = "manual", "판단문에 좌우 표기 없음 — 담당자 확인"
        elif applied & judged:
            status, note = "ok", None
        else:
            status = "mismatch"
            lab = {"L": "왼쪽", "R": "오른쪽", "B": "양측"}
            note = (f"신청은 {'·'.join(lab[s] for s in sorted(applied))}인데 판단문은 "
                    f"{'·'.join(lab[s] for s in sorted(judged))} — 오기 여부 확인 필요")
            warns += 1
        items.append({"wnd_sn": str(d["dis_id"]), "dis_id": d["dis_id"],  # 명세 ID 규약 + 구화면 병행
                      "name": d.get("name"), "applied": sorted(applied),
                      "judged": sorted(judged), "status": status, "note": note})
    return {"app_id": app_id, "items": items, "mismatch": warns,
            "note": "신청상이·판단문 좌우 표기 결정적 대조 — 확정은 담당자(HITL)"}


def check(app_id: int) -> dict:
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM application WHERE app_id=%s", (app_id,))
        app = cur.fetchone()
        if not app:
            return {"error": "안건 없음"}
        cur.execute("SELECT * FROM disability WHERE app_id=%s ORDER BY dis_id", (app_id,))
        diss = cur.fetchall()
        cur.execute("SELECT m.* FROM medical_record m JOIN disability d USING (dis_id)"
                    " WHERE d.app_id=%s", (app_id,))
        meds = cur.fetchall()
        cur.execute("SELECT doc_kind, content FROM official_doc WHERE app_id=%s", (app_id,))
        odocs = cur.fetchall()
        cur.execute("SELECT exams FROM scan_doc WHERE app_id=%s", (app_id,))
        scans = cur.fetchall()
        cur.execute("SELECT * FROM judgment_rule WHERE subcommittee=%s ORDER BY jr_id",
                    (str(app.get("subcommittee") or ""),))
        rules = cur.fetchall()

    # 보유 자료 텍스트 풀 — 필요서류 키워드 대조 대상
    pool = " ".join(filter(None,
        [f"{m.get('rec_type') or ''} {m.get('imaging') or ''} {m.get('finding') or ''}" for m in meds] +
        [f"{o.get('doc_kind') or ''} {o.get('content') or ''}" for o in odocs] +
        [str(s.get("exams") or "") for s in scans]))

    target = " ".join([app.get("review_content") or ""] + [d.get("name") or "" for d in diss])
    out = []
    for r in rules:
        try:
            if not re.search(r["disease_pattern"], target):
                continue
        except re.error:
            continue
        item = {"axis": r["axis"], "condition": r["condition"], "basis": r["basis"],
                "check_kind": r["check_kind"], "missing_docs": [], "note": None}
        if r["check_kind"] == "auto" and "급성" in r["axis"]:
            item["status"], item["note"] = _mri_check(diss[0] if diss else {}, meds)
        elif r["check_kind"] == "doc":
            missing = [k for k in (r["required_docs"] or []) if k.lower() not in pool.lower()]
            item["missing_docs"] = missing
            item["status"] = "ok" if not missing else "lack"
            if missing:
                item["note"] = "미확인 자료: " + ", ".join(missing)
        else:
            item["status"] = "manual"
        out.append(item)
    return {"app_id": app_id, "subcommittee": app.get("subcommittee"),
            "applicant": app.get("applicant"), "is_real": app.get("is_real"),
            "rules": out,
            "note": "정형화틀 v2.4 판단기준 자동대조 — 참고용이며 확정은 담당자·심의 의결로 함(HITL)"}
