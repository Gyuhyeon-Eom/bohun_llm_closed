# -*- coding: utf-8 -*-
"""심의서 통합 작성 (1~3장 란별) — 초안=LLM, 확정=담당자, 산출=결정적 조립.

구조 개편(260722): 요건심사 1~3장을 한 페이지에서 란(신청사항/관련자료/
관계법령·판단전제) 단위로 작성한다.
- 초안: 사건 자료 + 정형화틀 v2.4 분과모듈을 주입해 LLM이 란 서술 생성
- 수정: 담당자가 텍스트박스로 고침 → field_edit(draft_s1~s3)에 교정쌍 축적 (diff 팝업)
- 체크: 란마다 필수/선택 체크리스트 — 필수 완료가 의결서 조립 게이트
- 산출: assemble()이 확정된 란 텍스트 + 4장 결론 문안을 LLM 없이 합친다
"""
import json

import psycopg
from psycopg.rows import dict_row

from config.settings import PG_DSN

SECTIONS = ("s1", "s2", "s3")

SECTION_TITLE = {"s1": "1. 신청사항", "s2": "2. 관련자료", "s3": "3. 관계법령·판단의 전제"}

SECTION_GUIDES = {
    "s1": ("1장 '신청사항' 란: 가. 신청경위(언제·어디서·무엇을·어떻게·왜 육하원칙 정리, "
           "재신청 사건이면 과거 신청·처분 이력을 이력1, 이력2 순으로 기재), "
           "나. 신청상이(상이처명·부위 좌/우·KCD·발병년월), 현재시점 후유증·합병증 순으로 서술."),
    "s2": ("2장 '관련자료' 란: 가. 병적관련자료(입대·전역·병과·근무경력 이동 이력·휴가), "
           "나. 국가유공자 요건 사실 확인서(상이연월일·장소·최초부상명), "
           "다. 의무기록(시간순 — 최초 진료·영상판독·수술기록 중요문서 강조, 급성/진구성 표기), "
           "공적서류(발병경위서 등) 순으로 서술."),
    "s3": ("3장 '관계법령·판단의 전제' 란: 가. 관련법령(신분 기준 적용 조문과 원문 요지), "
           "나. 본 건 판단의 전제(관련 판례 요지, 유사사례 — 담당자 선별분과 인정/비인정 구분), "
           "다. 의학정보·분과 판단기준(해당 질환의 판단축을 사건 사실에 대응시켜) 순으로 서술."),
}

# 란별 체크리스트 (정형화틀 v2.4 공통뼈대 기준 — required=의결서 조립 필수)
CHECKLISTS = {
    "s1": [{"label": "신청경위 육하원칙 기재 확인", "required": True},
           {"label": "상이처·부위(좌/우) 명확 (불명확 시 전화조사)", "required": True},
           {"label": "재신청 이력 대조 (해당 시)", "required": False}],
    "s2": [{"label": "병적자료(입대·전역·근무경력) 확인", "required": True},
           {"label": "요건사실확인서 상이연월일·장소·최초부상명 대조", "required": True},
           {"label": "의무기록 시간순 검토 (최초진료·영상·수술 중요문서)", "required": True},
           {"label": "건강보험 요양급여내역(과거력) 조회", "required": False},
           {"label": "의학자문·재판독 결과 반영", "required": False}],
    "s3": [{"label": "적용 조문(신분 기준) 확인", "required": True},
           {"label": "분과 판단기준(정형화틀 모듈) 대조", "required": True},
           {"label": "유사사례 선별(제외·추가) 검토", "required": False},
           {"label": "판례 검토 (적재 시)", "required": False}],
}


def _init_checks(section):
    return [{**c, "checked": False} for c in CHECKLISTS[section]]


def get_all(app_id: int) -> dict:
    """란별 초안·체크 상태 (없는 란은 빈 골격으로)."""
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM case_draft WHERE app_id=%s", (app_id,))
        rows = {r["section"]: r for r in cur.fetchall()}
    out = {}
    for s in SECTIONS:
        r = rows.get(s)
        checks = (r or {}).get("checks")
        if isinstance(checks, str):
            checks = json.loads(checks)
        out[s] = {"title": SECTION_TITLE[s],
                  "content": (r or {}).get("content"),
                  "source": (r or {}).get("source"),
                  "checks": checks or _init_checks(s)}
    return out


def _upsert(cur, app_id, section, **cols):
    keys = ", ".join(cols)
    ph = ", ".join(["%s"] * len(cols))
    sets = ", ".join(f"{k}=EXCLUDED.{k}" for k in cols)
    cur.execute(f"INSERT INTO case_draft(app_id, section, {keys})"
                f" VALUES (%s,%s,{ph}) ON CONFLICT (app_id, section)"
                f" DO UPDATE SET {sets}, updated_at=now()",
                (app_id, section, *cols.values()))


def generate(app_id: int, section: str, llm, emb) -> dict:
    """란 초안 생성 — 사건 자료 + 분과모듈 주입 (기존 내용은 field_edit에 남기고 대체)."""
    if section not in SECTIONS:
        return {"error": "section=s1|s2|s3"}
    from core.subcommittee_modules import module_for
    from services import decision_doc
    app = decision_doc.build_doc(app_id, emb)
    if not app:
        return {"error": "안건 없음"}
    dossier = "\n\n".join(decision_doc._dossier(app, d) for d in app["disabilities"])
    if section == "s3":  # 법령·판례는 s3 서술에 필요
        laws = "\n".join(f"- {l['clause']}: {(l['passage'] or '원문 미적재')[:200]}" for l in app["laws"])
        dossier = f"[적용 법령]\n{laws}\n\n{dossier}"
    text = llm.generate("case_draft",
                        section_guide=SECTION_GUIDES[section],
                        module=module_for(app["subcommittee_info"]["no"]) or "(분과모듈 없음)",
                        dossier=dossier[:6000])
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT content FROM case_draft WHERE app_id=%s AND section=%s",
                    (app_id, section))
        row = cur.fetchone()
        if row and row[0] and row[0] != text:  # 재생성도 이력에 남긴다
            cur.execute("INSERT INTO field_edit(app_id, field, old_value, new_value, editor)"
                        " VALUES (%s,%s,%s,%s,'AI 재생성')",
                        (app_id, f"draft_{section}", row[0], text))
        _upsert(cur, app_id, section, content=text, source="llm")
        conn.commit()
    return {"ok": True, "section": section, "content": text}


def save(app_id: int, section: str, content: str, editor: str = "담당자") -> dict:
    """담당자 수정 저장 — 교정쌍(field_edit draft_*) 축적 → diff 팝업·정규화 학습 공유."""
    if section not in SECTIONS:
        return {"error": "section=s1|s2|s3"}
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT content FROM case_draft WHERE app_id=%s AND section=%s",
                    (app_id, section))
        row = cur.fetchone()
        old = row[0] if row else None
        _upsert(cur, app_id, section, content=content, source="manual")
        cur.execute("INSERT INTO field_edit(app_id, field, old_value, new_value, editor)"
                    " VALUES (%s,%s,%s,%s,%s)",
                    (app_id, f"draft_{section}", old, content, editor))
        conn.commit()
    return {"ok": True}


def set_check(app_id: int, section: str, idx: int, checked: bool) -> dict:
    if section not in SECTIONS:
        return {"error": "section=s1|s2|s3"}
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT checks FROM case_draft WHERE app_id=%s AND section=%s",
                    (app_id, section))
        row = cur.fetchone()
        checks = row["checks"] if row and row["checks"] else _init_checks(section)
        if isinstance(checks, str):
            checks = json.loads(checks)
        if not 0 <= idx < len(checks):
            return {"error": "idx 범위 밖"}
        checks[idx]["checked"] = bool(checked)
        _upsert(cur, app_id, section, checks=json.dumps(checks, ensure_ascii=False))
        conn.commit()
    return {"ok": True, "checks": checks}


def required_done(app_id: int) -> dict:
    """의결서 조립 게이트 — 전 란 필수 체크 완료 여부."""
    drafts = get_all(app_id)
    missing = [f"{SECTION_TITLE[s]}: {c['label']}"
               for s in SECTIONS for c in drafts[s]["checks"]
               if c.get("required") and not c.get("checked")]
    empty = [SECTION_TITLE[s] for s in SECTIONS if not drafts[s]["content"]]
    return {"ok": not missing and not empty, "missing_checks": missing, "empty_sections": empty}


def assemble(app_id: int) -> str:
    """심의의결서 본문 — 확정된 란 텍스트 + 4장 결론 문안을 LLM 없이 조립."""
    from services import decision_doc
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM application WHERE app_id=%s", (app_id,))
        app = cur.fetchone()
        cur.execute("""SELECT d.name, c.body_text, c.final_text FROM disability d
                       LEFT JOIN conclusion c ON c.dis_id=d.dis_id AND c.app_id=d.app_id
                         AND c.round=%s
                       WHERE d.app_id=%s ORDER BY d.dis_id""", (app["round"], app_id))
        cons = cur.fetchall()
    drafts = get_all(app_id)
    L = ["심 의 의 결 서", "",
         f"신청인: {app['applicant']}  /  접수번호: {app['recv_no']}  /  심의차수: {app['round']}차",
         f"심의내용: {app['review_content']}", ""]
    for s in SECTIONS:
        L += [f"■ {SECTION_TITLE[s]}", drafts[s]["content"] or "(미작성)", ""]
    L.append("■ 4. 종합판단")
    for c in cons:
        L.append(f"○ {c['name']}")
        if c["body_text"]:
            L.append(c["body_text"])
        if c["final_text"]:
            L.append(f"결론: {c['final_text']}")
        L.append("")
    L.append("※ 본 문서는 담당자가 확정한 란 텍스트를 기계적으로 결합해 생성되었습니다 (생성 LLM 미사용).")
    return "\n".join(L)
