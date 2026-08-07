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

# 실물 심의의결서(제2분과, 260725 반입 — 전 분과 공통 뼈대) 목차·문체 반영:
# 개조식 '~함/~음/~됨' 종결, 날짜 'YYYY. M. D.' 표기, 상이처별 '- 부위:' 블록,
# 자료는 가./나./다. → 1) 2) → 가) 나) 위계. 질환별 판단축만 분과 모듈로 치환.
SECTION_GUIDES = {
    "s1": ("1장 '신청사항' 란 — 실물 의결서 구조를 따른다. "
           "가. 신청경위: 서두 한 문장 — '신청인(성명, 생년)은 입대일 입대, (임관일 임관,) "
           "전역(예정)일 전역 (예정)(신분·계급)인 ○○로 다음과 같이 진술하며 등록 신청함.' "
           "(재신청 계열이면 신청구분과 심의차수를 이 문장에 함께 명시하고, "
           "과거 신청·처분 이력을 이력1, 이력2 순으로 기재). "
           "이어 상이처별 개조식 블록: '- 부위(상이처): YYYY. M. D. 사건·경위, 이후 진단·수술·"
           "치료 경과를 시간순으로.' 마지막에 현재시점 후유증·합병증. "
           "나. 신청상이: 신청서 기재 상이처명 그대로 (부위 좌/우·KCD·발병년월)."),
    "s2": ("2장 '관련자료' 란 — 자료 종류별 가./나./다. 위계, 각 자료는 발급일·발급기관을 자료 표기 그대로 병기. "
           "가. 병적 관련 자료: 1) 전역(예정)확인서 — 입대일자·임관일자·전역(예정)일자, "
           "2) 인사자력표 — 근무경력(기간~부대/직책 시간순)·휴가내역. "
           "나. 국가유공자 요건 관련 사실 확인서(발급일·발급기관): 상이연월일·상이장소·상이원인·최초 질병/부상명. "
           "다. 의무기록: 상이처별 [부위] 표제 후 병원·차수별 1) 2) — 외래기록지는 <주소>·<발병일시>·<현병력>·<계획> "
           "등 원문 항목을 인용, 수술기록지는 수술 전/후 진단명·수술명·수술 시 발견사항, 입퇴원요약 순. "
           "(있으면) 군병원 영상자료에 대한 위원회 재판독 결과 — 날짜별 '최근 외상'/'진구성' 소견 명시, "
           "보훈심사위원회에서 확인한 자료(사실조회 회신 등)."),
    "s3": ("3장 '관련법령 및 판단의 전제, 참고자료' 란 — 실물 구조. "
           "가. 관련법령: 적용 조문별 1) 2) — '「법률명」 제N조제N항제N호 전단에 따르면, …' 형식으로 "
           "조문 요지를 사건 신분에 맞춰 서술 (예우법·보상법 + 각 시행령 [별표 1]). "
           "나. 본 건 판단의 전제 — 다음 두 문단은 고정 문안으로 그대로 쓴다: "
           "'1) 대법원 판례(대법원 1993. 6. 29. 선고 92누14762 판결 등)에서 판시된 바와 같이 "
           "보훈심사위원회와 국가보훈부장관은 신청인이 소속하였던 기관의 장이 확인·통보한 자료에 "
           "구속되지 않고 통보된 자료 등을 참작하여 국가유공자 등의 요건에 해당하는지 여부를 "
           "독자적으로 심의·결정함.' "
           "'2) 이에 보훈심사위원회에서는 신청인의 발병 경위 등에 대한 사실관계를 확인하고, 전문의 등 "
           "외부 전문가가 위원으로 참여한 심사회의에서 실체적·의학적 사실관계 등에 대해 심층적으로 "
           "검토하여 「국가유공자법 시행령」 [별표 1]과 「보훈보상대상자법 시행령」 [별표 1]에 규정된 "
           "요건의 기준 및 범위에 각각 해당하는지 여부를 심의함.' "
           "다. 참고자료: 유사사례(담당자 선별분 — 인정/비인정 구분)·판례 발췌·분과 판단기준(정형화틀 모듈)의 "
           "판단축을 사건 사실에 대응시켜 요지 서술."),
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
    # 자료 우선순위(담당자 드래그 정렬, 260725) — 상위 자료의 사실을 우선 인용하도록 주입
    try:
        from services import case_file as _cf
        files = _cf.list_files(app_id)
        if files:
            flist = "\n".join(f"{i+1}. [{f['kind']}] {f['title']}" for i, f in enumerate(files[:12]))
            dossier = f"[자료 우선순위 — 번호가 앞설수록 우선 인용·비중 높게]\n{flist}\n\n{dossier}"
    except Exception:
        pass
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
    """의결서 조립 게이트 — 전 란 필수 체크 + 4장 종합판단 완료 여부.
    (260725: 의결서 = 초안(1~3장) + 종합판단(4장)이므로 판단내용 미생성 상이처가 있으면 미충족)"""
    drafts = get_all(app_id)
    missing = [f"{SECTION_TITLE[s]}: {c['label']}"
               for s in SECTIONS for c in drafts[s]["checks"]
               if c.get("required") and not c.get("checked")]
    empty = [SECTION_TITLE[s] for s in SECTIONS if not drafts[s]["content"]]
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("""SELECT d.name FROM disability d
                       JOIN application a USING (app_id)
                       LEFT JOIN conclusion c ON c.dis_id=d.dis_id AND c.app_id=d.app_id
                         AND c.round=a.round
                       WHERE d.app_id=%s AND (c.body_text IS NULL OR c.body_text='')
                       ORDER BY d.dis_id""", (app_id,))
        missing_judgment = [r["name"] for r in cur.fetchall()]
    return {"ok": not missing and not empty and not missing_judgment,
            "missing_checks": missing, "empty_sections": empty,
            "missing_judgment": missing_judgment}


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
    # 헤더 표 — 실물 의결서(담당자|신청인|생년월일|심사구분|배정일|검토의견) 대응
    verdicts = [c["final_text"] for c in cons if c.get("final_text")]
    verdict = "비해당" if any("비해당" in v or "해당하지 않" in v for v in verdicts) else \
              ("해당" if verdicts else "검토중")
    L = ["심 의 의 결 서", "",
         f"담당자: {app.get('assignee') or '미배정'}  |  신청인: {app['applicant']}"
         f"  |  생년월일: {app.get('birth_year') or '—'}"
         f"  |  심사구분: {app.get('track') or '일반'}"
         f"  |  배정일: {app.get('assigned_date') or '—'}  |  검토의견: {verdict}",
         f"접수번호: {app['recv_no']}  /  안건번호: {app.get('agenda_no') or '—'}"
         f"  /  심의차수: {app['round']}차  /  심의내용: {app['review_content']}", ""]
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


def draft_history(app_id: int, section: str | None = None) -> list[dict]:
    """작성이력 목록 (검토의견 37 — 사업단 확답) — new_value가 그 시점의 란 본문.
    field_edit(draft_*)를 그대로 읽는다 (생성·재생성·담당자 수정·복원 모두 축적됨)."""
    where = "app_id=%s AND field LIKE 'draft_%%'"
    params: list = [app_id]
    if section:
        where += " AND field=%s"; params.append(f"draft_{section}")
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT fe_id, field, editor, created_at,"
                    f" left(new_value, 200) AS preview, length(new_value) AS chars"
                    f" FROM field_edit WHERE {where} ORDER BY fe_id DESC LIMIT 100", params)
        return [{**r, "section": r["field"].removeprefix("draft_")} for r in cur.fetchall()]


def restore(app_id: int, fe_id: int, editor: str = "담당자") -> dict:
    """이력 시점 본문을 현재 란에 재반영 — 복원 자체도 save() 경유라 다시 이력에 남는다."""
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT field, new_value FROM field_edit"
                    " WHERE fe_id=%s AND app_id=%s AND field LIKE 'draft_%%'", (fe_id, app_id))
        row = cur.fetchone()
    if not row:
        return {"error": "해당 안건의 초안 이력이 아닙니다"}
    section = row["field"].removeprefix("draft_")
    r = save(app_id, section, row["new_value"] or "", editor=f"{editor}(이력 #{fe_id} 복원)")
    return {**r, "section": section, "restored_from": fe_id}
