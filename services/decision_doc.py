"""심의의결서 v2 — 정형화틀(공통뼈대 4장) + DB명세서 사건 데이터 기반.

흐름(HITL): 사건 자료(1~3장)는 DB·RAG에서 결정적으로 조립 -> 담당자가 상이처별
이원 판단(국가유공자/보훈보상 축)을 선택 -> LLM이 그 분기의 '판단내용' 문안 작성
-> 담당자 수정 후 확정(conclusion 스냅샷).
"""
import psycopg
from psycopg.rows import dict_row
from config.settings import PG_DSN
from core.llm_client import LLMClient
from core.retrieval import hybrid_search
from core.graph import cases_by_kcd
from core.subcommittee import profiles, MANUAL_DOCTYPE
from services.review_doc import _expand_clause, _strip_markdown


def clauses_for(duty_type: str, is_death: bool) -> tuple[str, str]:
    """공통뼈대 3.가 — 신분×유형별 적용 조문 (결정적).
    TODO(확인): 전상·전몰(1분과, 4-1-4/4-1-3) 분기 조건은 참전 여부 데이터 확보 후."""
    soldier = duty_type in ("병사", "부사관", "장교", "경찰")
    if soldier:
        return ("예우법 4-1-5", "보상법 2-1-1") if is_death else ("예우법 4-1-6", "보상법 2-1-2")
    return ("예우법 4-1-14", "보상법 2-1-3") if is_death else ("예우법 4-1-15", "보상법 2-1-4")


def _q(cur, sql, args=()):
    cur.execute(sql, args)
    return cur.fetchall()


def load_case(app_id: int) -> dict | None:
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        apps = _q(cur, "SELECT * FROM application WHERE app_id=%s", (app_id,))
        if not apps:
            return None
        app = apps[0]
        app["disabilities"] = _q(cur, "SELECT * FROM disability WHERE app_id=%s ORDER BY dis_id", (app_id,))
        for d in app["disabilities"]:
            d["medical"] = _q(cur, "SELECT * FROM medical_record WHERE dis_id=%s ORDER BY rec_date", (d["dis_id"],))
        svc = _q(cur, "SELECT * FROM service_record WHERE app_id=%s", (app_id,))
        app["service"] = svc[0] if svc else {}
        app["conclusions"] = {c["dis_id"]: c for c in
                              _q(cur, "SELECT * FROM conclusion WHERE app_id=%s AND round=%s", (app_id, app["round"]))}
        app["official_docs"] = _q(cur, "SELECT * FROM official_doc WHERE app_id=%s ORDER BY doc_date, od_id", (app_id,))
        return app


def build_doc(app_id: int, emb) -> dict | None:
    """공통뼈대 1~3장 + 4장 골격. 근거(법령·의학정보·유사사례)는 RAG로 수집."""
    app = load_case(app_id)
    if not app:
        return None
    sub = profiles()[app["subcommittee"]]
    yeu, bosang = clauses_for(app["duty_type"], app["is_death"])

    # 3.가 관련법령: 적용 조문 원문 발췌 (RAG)
    laws = []
    for cl in (yeu, bosang):
        q = _expand_clause(cl)
        hits = hybrid_search(q, emb.encode([q])[0], top_k=1, doc_type="법령")
        laws.append({"clause": cl, "passage": hits[0]["content"][:400] if hits else None,
                     "source": hits[0]["source_path"].split("#")[-1] if hits else None})

    for d in app["disabilities"]:
        d["yeu_clause"], d["bosang_clause"] = yeu, bosang
        # 3.다 의학정보 + 분과모듈 판단기준 (RAG, 분과 매뉴얼 우선)
        q = f"{d['name']} 판단기준 심사 포인트"
        hits = hybrid_search(q, emb.encode([q])[0], top_k=4,
                             doc_type=MANUAL_DOCTYPE[app["subcommittee"]])
        # 무관 발췌 차단: 상병명·부위 토큰(2자+)이 하나도 안 겹치는 청크는 버린다
        # (통합 문서에 타 분과 내용 혼재 + 임베더 품질 편차 대비 어휘 검증)
        import re as _re
        # 판별 토큰 = 상병명·부위의 3자 이상 낱말 (질의문 전체를 쓰면 '외상' 같은 범용어로 오통과)
        toks = {t for t in _re.findall(r"[가-힣A-Za-z]{3,}", f"{d['name']} {d.get('body_side') or ''}")}
        d["criteria"] = [{"content": h["content"][:500], "source": sub["manual"]}
                         for h in hits
                         if any(t in h["content"] for t in toks)][:2]
        # 3.나 유사사례 (그래프)
        d["similar"] = cases_by_kcd([d["kcd_code"]], n=3) if d["kcd_code"] else []
        d["conclusion"] = app["conclusions"].get(d["dis_id"])
        # 4.나 AI 사전 판단: 사건 자료(OCR)·유사사례 기반 두 축 예측 (담당자 확인·수정 전 추천값)
        d["predicted"] = _predict_axes(app, d)

    app["subcommittee_info"] = {"no": app["subcommittee"], "name": sub["name"],
                                "specialty": sub["specialty"]}
    app["checklist"] = sub["checklist"]
    app["laws"] = laws
    return app


def _predict_axes(app: dict, d: dict) -> dict:
    """사건 자료(의무기록·과거력·재판독)와 유사사례로 두 축(국가유공자/보훈보상)을 사전 예측.
    반환: {yeu_result, bosang_result, confidence, basis[]} — 담당자 확인·수정 전 추천값."""
    basis = []
    score = 0  # +면 해당 방향, -면 비해당 방향

    sims = d.get("similar") or []
    yes = sum(1 for s in sims if (s.get("decision") or "").strip() == "해당")
    no = sum(1 for s in sims if (s.get("decision") or "").strip() == "비해당")
    if yes or no:
        if yes >= no:
            score += 1; basis.append(f"유사사례 {yes+no}건 중 '해당' {yes}건 다수")
        else:
            score -= 1; basis.append(f"유사사례 {yes+no}건 중 '비해당' {no}건 다수")

    med = d.get("medical") or []
    if any((m.get("chronic") == "Y") for m in med):
        score -= 1; basis.append("재판독 소견상 진구성(퇴행성) — 불인정 방향")
    if any((m.get("chronic") == "N") for m in med):
        score += 1; basis.append("영상 재판독상 급성 외상 소견 — 인정 방향")
    if any((m.get("finding") and "불일치" in m["finding"]) for m in med):
        score -= 1; basis.append("공적서류·의무기록 간 발생경위 기재 불일치 — 신중")

    sv = app.get("service") or {}
    if sv.get("discharge_date") is None:
        basis.append("복무 중(현역) — 공무 관련성 판단 시 참작")

    yeu = "해당" if score > 0 else "비해당"
    bosang = "해당" if score >= 0 else "비해당"
    conf = "높음" if abs(score) >= 2 else ("보통" if abs(score) == 1 else "낮음")
    if not basis:
        basis.append("사건 자료 신호 부족 — 담당자 직접 판단 필요")
    return {"yeu_result": yeu, "bosang_result": bosang, "confidence": conf, "basis": basis}


def _strip_artifacts(text: str) -> str:
    """LLM 양식 이탈 제거: 선행 제목류('심의의결서', '종합 판단' 등)와
    말미 서명·권고 블록('서명', '[보훈심사관 이름]', '권고 사항' 이후)을 걷어낸다."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        t = lines[i].strip()
        if not t or (len(t) < 34
                     and any(k in t for k in ("심의의결서", "심 의 의 결", "종합 판단", "판단 내용", "국가보훈부"))
                     and not t.endswith(("다.", "함.", "음.", "됨."))):
            i += 1
            continue
        break
    lines = lines[i:]
    cut = len(lines)
    for j, ln in enumerate(lines):
        t = ln.strip()
        if t in ("서명",) or t.startswith(("[보훈심사관", "[날짜", "권고 사항", "권고사항", "서명:")):
            cut = j
            break
    return "\n".join(lines[:cut]).strip()


def _dossier(app: dict, d: dict) -> str:
    """LLM에 주입할 사건 자료 (해당 상이처 관점)."""
    s = app.get("service") or {}
    lines = [f"신청인: {app['applicant']} ({app['duty_type']}, 심의차수 {app['round']}차)",
             f"신청경위: {app['apply_story']}",
             f"현재 후유증: {app.get('aftermath') or '기재 없음'}",
             f"신청상이: {d['name']} ({d['body_side'] or '부위 표기 없음'}, KCD {d['kcd_code']}, 발병 {d['onset_ym']})",
             f"요건사실확인서: 상이연월일 {d['fact_date']} / 장소 {d['fact_place']} / 최초부상명 {d['fact_first_dx']}",
             f"병적: 입대 {s.get('enlist_date')} / 전역 {s.get('discharge_date') or '복무중'} / {s.get('branch')} / {s.get('career')}"]
    if s.get("leave_note"):
        lines.append(f"휴가내역: {s['leave_note']}")
    if s.get("overtime"):
        lines.append(f"초과근무·특별업무: {s['overtime']}")
    odocs = [o for o in app.get("official_docs", [])
             if o["dis_id"] in (None, d["dis_id"])]
    if odocs:
        lines.append("공적서류(발병경위서·군 심사 결정·사실조회 등):")
        for o in odocs:
            lines.append(f"  - [{o['doc_kind']}] {o['doc_date']} {o['issuer']}: {o['content'][:220]}")
    lines.append("의무기록(시간순):")
    for m in d["medical"]:
        seg = f"  - {m['rec_date']} [{m['period']}/{m['rec_type']}] {m['hospital']}: {m['diagnosis'] or m['chief'] or ''}"
        if m["imaging"]:
            seg += f" ({m['imaging']}: {m['finding']})"
        elif m["finding"]:
            seg += f" — {m['finding']}"
        if m["surgery"]:
            seg += f" / 수술: {m['surgery']}"
        if m["chronic"]:
            seg += f" / {'진구성' if m['chronic'] == 'Y' else '급성'} 소견"
        lines.append(seg)
    return "\n".join(lines)


def draft_judgment(app_id: int, dis_id: int, yeu_result: str, bosang_result: str,
                   llm: LLMClient, emb) -> dict:
    """담당자 선택(이원 판단) -> LLM 판단내용 초안 + 결론문안 -> conclusion upsert."""
    app = build_doc(app_id, emb)
    d = next(x for x in app["disabilities"] if x["dis_id"] == dis_id)
    criteria = "\n".join(c["content"] for c in d["criteria"]) or "(분과 판단기준 발췌 없음)"
    # 요건심사 정형화틀 v2 — 분과별 판단모듈을 4.나 가)(2) 지점 기준으로 주입
    from core.subcommittee_modules import module_for
    module = module_for(app["subcommittee_info"]["no"])
    if module:
        criteria = f"{module}\n\n[분과 매뉴얼 발췌]\n{criteria}"
    body = _strip_markdown(llm.generate(
        "judgment", review_content=app["review_content"],
        subcommittee=app["subcommittee_info"]["name"],
        onset=f"{d['onset_ym']} {d['onset_story']}", dis_name=d["name"],
        yeu_clause=d["yeu_clause"], yeu_result=yeu_result,
        bosang_clause=d["bosang_clause"], bosang_result=bosang_result,
        dossier=_dossier(app, d), criteria=criteria))
    body = _strip_artifacts(body)
    final = (f"신청상이 '{d['name']}'에 대하여 {d['yeu_clause']}에서 규정한 요건에 {yeu_result}, "
             f"{d['bosang_clause']}에서 규정한 요건에 {bosang_result}함.")
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO conclusion(app_id, dis_id, round, yeu_clause, yeu_result,
                                   bosang_clause, bosang_result, body_text, final_text, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'작성중')
            ON CONFLICT (app_id, dis_id, round) DO UPDATE SET
              yeu_result=EXCLUDED.yeu_result, bosang_result=EXCLUDED.bosang_result,
              body_text=EXCLUDED.body_text, final_text=EXCLUDED.final_text, status='작성중'""",
                    (app_id, dis_id, app["round"], d["yeu_clause"], yeu_result,
                     d["bosang_clause"], bosang_result, body, final))
        cur.execute("UPDATE application SET status='심사중' WHERE app_id=%s AND status='접수'", (app_id,))
    return {"dis_id": dis_id, "body_text": body, "final_text": final,
            "yeu_result": yeu_result, "bosang_result": bosang_result}


def finalize(app_id: int, dis_id: int, body_text: str | None = None) -> dict:
    """담당자 수정 반영 후 결론 확정 (스냅샷 동결)."""
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        if body_text is not None:
            cur.execute("UPDATE conclusion SET body_text=%s WHERE app_id=%s AND dis_id=%s", (body_text, app_id, dis_id))
        cur.execute("UPDATE conclusion SET status='확정', decided_at=now() WHERE app_id=%s AND dis_id=%s", (app_id, dis_id))
        cur.execute("""UPDATE application SET status='의결' WHERE app_id=%s AND NOT EXISTS
                       (SELECT 1 FROM conclusion c JOIN disability d USING (dis_id)
                        WHERE d.app_id=%s AND c.status != '확정')""", (app_id, app_id))
    return {"status": "확정"}


# ── 산출물 export (정형화틀 v2 전체 텍스트 → txt / pdf) ──
def _safe_name(text: str) -> str:
    """파일명에 못 쓰는 문자 제거 (한글·공백은 유지)."""
    return "".join(c for c in text if c not in '/\\:*?"<>|').strip()


def _full_text(app_id: int, emb, dis_id: int | None = None) -> tuple[str, str]:
    """공통뼈대 1~4장 정형화틀 전체를 계층 들여쓰기로 조립. (제목, 본문) 반환.
    dis_id 지정 시 해당 상이처만 담은 개별본 생성 (상이처 여러 건 안건의 분할 산출)."""
    s = build_doc(app_id, emb)
    if not s:
        return "심의의결서", "안건을 찾을 수 없습니다."
    if dis_id is not None:
        s["disabilities"] = [d for d in s["disabilities"] if d["dis_id"] == dis_id]
        if not s["disabilities"]:
            return "심의의결서", "상이처를 찾을 수 없습니다."
    sv = s.get("service") or {}
    confirmed = all((d.get("conclusion") or {}).get("status") == "확정" for d in s["disabilities"])

    # 들여쓰기 단계: 0=대분류, 1=중분류(가.나.), 2=세부(①,-), 3=내용
    IND = ["", "    ", "        ", "            "]
    L = []

    def line(level, text):
        L.append(IND[level] + text)

    def wrap(level, text, width=78):
        """긴 텍스트를 폭에 맞춰 접되, 이어지는 줄은 한 단계 더 들여쓰기."""
        text = str(text or "—")
        pad = IND[level]
        cont = IND[min(level + 1, 3)]
        cur, first = "", True
        for word in text.split(" "):
            if len(cur) + len(word) + 1 > width:
                L.append((pad if first else cont) + cur.rstrip())
                cur, first = word + " ", False
            else:
                cur += word + " "
        if cur.strip():
            L.append((pad if first else cont) + cur.rstrip())

    sep = "─" * 60
    anyyes = any((d.get("conclusion") or {}).get("yeu_result") == "해당"
                 or (d.get("conclusion") or {}).get("bosang_result") == "해당" for d in s["disabilities"])
    verdict = ("해당(일부 포함)" if anyyes else "비해당") if confirmed else "검토중"
    # ── 표제 (실물 표지 헤더) ──
    L.append("심 의 의 결 서" + ("" if confirmed else "  (초안)"))
    L.append(f"담당자 {s.get('assignee') or s['subcommittee_info']['name']}  │  신청인 {s['applicant']}  │  생년 {s.get('birth_year','—')}"
             f"  │  심사구분 {s.get('track') or '일반'}  │  배정일 {s.get('assigned_date') or '—'}  │  검토의견 {verdict}")
    L.append(f"접수번호 {s['recv_no']}  │  {s['review_content']} {s['round']}차  │  담당 {s['subcommittee_info']['name']}")
    L.append(sep)
    L.append("")

    # ── 1. 신청사항 ──
    line(0, "1. 신청사항")
    line(1, "가. 신청경위")
    wrap(2, s.get("apply_story") or "—")
    line(2, "③ 현재시점 후유증·합병증")
    wrap(3, s.get("aftermath") or "—")
    line(1, "나. 신청상이")
    for d in s["disabilities"]:
        line(2, f"· {d['name']}  ({d.get('body_side') or '—'} · KCD {d['kcd_code']} · 발병 {d['onset_ym']})")
    L.append("")

    # ── 2. 관련자료 ──
    line(0, "2. 관련자료")
    line(1, "가. 병적관련자료")
    line(2, f"입대 {sv.get('enlist_date') or '—'} / 전역 {sv.get('discharge_date') or '복무중'}"
            f" / {sv.get('branch') or ''}")
    if sv.get("career"):
        wrap(2, f"근무경력: {sv['career']}")
    if sv.get("leave_note"):
        wrap(2, f"휴가내역: {sv['leave_note']}")
    if sv.get("overtime"):
        wrap(2, f"초과근무·특별업무: {sv['overtime']}")
    for d in s["disabilities"]:
        line(1, f"나. 요건사실확인서 — {d['name']}")
        line(2, f"상이연월일 {d.get('fact_date') or '—'} / 상이장소 {d.get('fact_place') or '—'}")
        line(2, f"최초부상명 {d.get('fact_first_dx') or '—'}")
        line(1, f"다. 의무기록 — {d['name']} (진료 시간순)")
        for m in d.get("medical", []):
            if m.get("rec_type") == "재판독":   # 위원회 재판독은 별도 절(마.)에 수록
                continue
            head = f"- {m.get('rec_date') or '-'} [{m.get('period') or '-'}/{m.get('rec_type') or '-'}] {m.get('hospital') or ''}"
            line(2, head)
            detail = m.get("diagnosis") or m.get("chief") or ""
            if m.get("imaging"):
                detail += f" (영상 {m['imaging']}: {m.get('finding') or ''})"
            elif m.get("finding"):
                detail += f" — {m['finding']}"
            if m.get("surgery"):
                detail += f" / 수술: {m['surgery']}"
            if m.get("chronic") == "Y":
                detail += "  ※재판독: 진구성"
            elif m.get("chronic") == "N":
                detail += "  ※재판독: 급성"
            if detail.strip():
                wrap(3, detail)
    odocs = s.get("official_docs") or []
    others = [o for o in odocs if o.get("doc_kind") != "사실조회 회신"]
    facts = [o for o in odocs if o.get("doc_kind") == "사실조회 회신"]
    if others:
        line(1, "라. 발병경위서·공무상병인증서 등 공적서류")
        for i, o in enumerate(others, 1):
            line(2, f"{i}) {o['doc_kind']} ({o.get('doc_date') or '—'}. {o.get('issuer') or '—'})")
            wrap(3, o.get("content") or "")
    rereads = [(d, m) for d in s["disabilities"] for m in d.get("medical", [])
               if m.get("rec_type") == "재판독"]
    if rereads:
        line(1, "마. 군병원 영상자료에 대한 위원회 재판독 결과")
        for d, m in rereads:
            wrap(2, f"- {m.get('rec_date') or ''}. [{d['name']}] {m.get('imaging') or ''}:"
                    f" {m.get('finding') or ''} — {'진구성' if m.get('chronic') == 'Y' else '최근 외상'}")
    if facts:
        line(1, "바. 보훈심사위원회에서 확인한 자료 (사실조회)")
        for o in facts:
            wrap(2, f"- ({o.get('doc_date') or '—'}. {o.get('issuer') or '—'}) {o.get('content') or ''}")
    L.append("")

    # ── 3. 관련법령 및 판단의 전제 ──
    line(0, "3. 관련법령 및 판단의 전제, 참고자료")
    line(1, "가. 관련법령")
    for i, l in enumerate(s.get("laws", []), 1):
        line(2, f"{i}) {l['clause']}")
        if l.get("passage"):
            wrap(3, " ".join(str(l["passage"]).split())[:500])
    line(1, "나. 본 건 판단의 전제")
    wrap(2, "1) 대법원 판례(대법원 1993. 6. 29. 선고 92누14762 판결 등)에서 판시된 바와 같이 "
            "보훈심사위원회와 국가보훈부장관은 신청인이 소속하였던 기관의 장이 확인·통보한 자료에 "
            "구속되지 않고 통보된 자료 등을 참작하여 국가유공자 등의 요건에 해당하는지의 여부를 "
            "독자적으로 심의·결정함.")
    wrap(2, "2) 이에 보훈심사위원회에서는 신청인의 발병 경위 등에 대한 사실관계를 확인하고, 전문의 등 "
            "외부 전문가가 위원으로 참여한 심사회의에서 실체적·의학적 사실관계 등에 대해 심층적으로 검토함.")
    from core.subcommittee_modules import module_for
    line(1, "다. 의학정보·분과 판단기준")
    wrap(2, " ".join(module_for(s["subcommittee_info"]["no"]).split())[:400])
    for d in s["disabilities"]:
        for c in d.get("criteria", [])[:1]:
            wrap(2, f"[{d['name']} 관련 매뉴얼 발췌] " + " ".join(c["content"].split())[:280] + f" ({c['source']})")
    L.append("")

    # ── 4. 종합 판단 ──
    line(0, "4. 종합 판단")
    line(1, "가. 신청경위 요약")
    wrap(2, s.get("apply_story") or "—")
    line(1, "나·다. 판단내용 및 결론")
    for d in s["disabilities"]:
        c = d.get("conclusion") or {}
        line(2, f"[ {d['name']} ]")
        if c.get("body_text"):
            for para in str(c["body_text"]).split("\n"):
                if para.strip():
                    wrap(3, para.strip())
                else:
                    L.append("")
            line(2, f"⇒ 결론: {c.get('final_text') or ''}  ({c.get('status') or ''})")
        else:
            wrap(3, "(판단내용 미작성 — 이원 판단 선택 후 생성 필요)")
        L.append("")

    if confirmed:
        line(1, "다. 결론")
        mil = s.get("duty_type") in ("병사", "부사관", "장교")
        yeu_name = ("국가유공자(순직군경)" if s.get("is_death") else "국가유공자(공상군경)") if mil \
                   else ("국가유공자(순직공무원)" if s.get("is_death") else "국가유공자(공상공무원)")
        bo_name = ("보훈보상대상자(재해사망군경)" if s.get("is_death") else "보훈보상대상자(재해부상군경)") if mil \
                  else ("보훈보상대상자(재해사망공무원)" if s.get("is_death") else "보훈보상대상자(재해부상공무원)")
        if not anyyes:
            names = ", ".join(f"'{d['name']}'" for d in s["disabilities"])
            wrap(2, f"신청상이 {names}에 대하여 「국가유공자 등 예우 및 지원에 관한 법률」과 "
                    f"「보훈보상대상자 지원에 관한 법률」에서 규정한 {yeu_name} 및 {bo_name} 요건에 "
                    f"각각 해당하지 않는 것으로 검토함.")
        else:
            for d in s["disabilities"]:
                c = d.get("conclusion") or {}
                wrap(2, f"신청상이 '{d['name']}'은(는) {yeu_name} 요건에 {c.get('yeu_result','—')}, "
                        f"{bo_name} 요건에 {c.get('bosang_result','—')}하는 것으로 검토함.")
        L.append("")
    L.append(sep)
    wrap(0, "※ 본 문서는 AI 지원으로 작성된 심의의결서이며, 담당자 확정 및 보훈심사위원회 의결로 효력이 발생함.")
    title = f"심의의결서_{s['recv_no']}"
    if dis_id is not None:
        d = s["disabilities"][0]
        title += "_" + _safe_name(f"{d.get('body_side') or ''} {d['name']}".strip())[:40]
    return title, "\n".join(L)


def export_txt(app_id: int, emb, dis_id: int | None = None) -> tuple[str, str]:
    import os, tempfile
    title, text = _full_text(app_id, emb, dis_id)
    path = os.path.join(tempfile.gettempdir(), f"{title}.txt")
    # utf-8-sig(BOM): 윈도우 메모장 등에서 인코딩 자동 인식되어 한글이 깨지지 않음
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(text)
    return f"{title}.txt", path


def export_split(app_id: int, emb, fmt: str = "txt") -> tuple[str, str]:
    """상이처가 여러 건인 안건의 상이처별 개별본을 zip으로 일괄 산출. fmt=txt|pdf."""
    import os
    import tempfile
    import zipfile
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        rows = _q(cur, "SELECT dis_id FROM disability WHERE app_id=%s ORDER BY dis_id", (app_id,))
        recv = _q(cur, "SELECT recv_no FROM application WHERE app_id=%s", (app_id,))
    if not rows or not recv:
        raise ValueError("안건 또는 상이처 없음")
    fname = f"심의의결서_{recv[0]['recv_no']}_상이처별_{len(rows)}건_{fmt}.zip"
    path = os.path.join(tempfile.gettempdir(), fname)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        seen = set()
        for r in rows:
            inner_name, inner_path = (export_pdf if fmt == "pdf" else export_txt)(app_id, emb, r["dis_id"])
            if inner_name in seen:  # 동일 상이처명 중복 대비
                base, ext = os.path.splitext(inner_name)
                inner_name = f"{base}_{r['dis_id']}{ext}"
            seen.add(inner_name)
            zf.write(inner_path, arcname=inner_name)
    return fname, path


def export_batch(app_ids: list[int], emb, fmt: str = "txt") -> tuple[str, str]:
    """여러 안건의 심의의결서를 zip으로 일괄 산출. fmt=txt|pdf."""
    import os
    import tempfile
    import zipfile
    from datetime import date

    fname = f"심의의결서_일괄_{len(app_ids)}건_{date.today().strftime('%Y%m%d')}.zip"
    path = os.path.join(tempfile.gettempdir(), fname)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for app_id in app_ids:
            inner_name, inner_path = (export_pdf if fmt == "pdf" else export_txt)(app_id, emb)
            zf.write(inner_path, arcname=inner_name)
    return fname, path


def export_pdf(app_id: int, emb, dis_id: int | None = None) -> tuple[str, str]:
    """reportlab으로 한글 PDF 생성. reportlab 미설치 시 함수 호출 시점에만 오류."""
    import os, tempfile
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    title, text = _full_text(app_id, emb, dis_id)
    # 한글 폰트 등록 (시스템 폰트 후보 탐색)
    font_name = "Helvetica"
    # 한글 폰트 등록: 여러 후보 탐색. (경로, subfontIndex) — .ttc는 인덱스 필요할 수 있음
    font_candidates = [
        ("/System/Library/Fonts/Supplemental/AppleGothic.ttf", None),
        ("/System/Library/Fonts/AppleSDGothicNeo.ttc", 0),
        ("/Library/Fonts/AppleGothic.ttf", None),
        ("/System/Library/Fonts/Supplemental/NanumGothic.ttf", None),
        ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", None),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
        ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", 0),
    ]
    registered = False
    for fp, idx in font_candidates:
        if not os.path.exists(fp):
            continue
        try:
            if idx is not None:
                pdfmetrics.registerFont(TTFont("KFont", fp, subfontIndex=idx))
            else:
                pdfmetrics.registerFont(TTFont("KFont", fp))
            font_name = "KFont"
            registered = True
            break
        except Exception:
            continue
    if not registered:
        # 마지막 수단: reportlab 내장 CID 폰트(별도 파일 불필요, 한글 지원)
        try:
            from reportlab.pdfbase.cidfonts import UnicodeCIDFont
            pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
            font_name = "HYSMyeongJo-Medium"
            registered = True
        except Exception:
            pass
    path = os.path.join(tempfile.gettempdir(), f"{title}.pdf")
    c = canvas.Canvas(path, pagesize=A4)
    W, H = A4
    x, y = 42, H - 50
    size = 9
    max_w = W - x * 2                       # 실제 사용 가능 폭(pt)
    c.setFont(font_name, size)

    def _fit(line):
        """실측 폭 기준 줄바꿈 — 한글(전각)·영문 혼용에도 잘리지 않음.
        들여쓰기를 보존하고 이어지는 줄은 두 칸 더 들여쓴다. 공백 경계 우선."""
        stripped = line.lstrip(" ")
        prefix = " " * (len(line) - len(stripped))
        cont = prefix + "  "
        out, cur, first = [], "", True
        for ch in stripped:
            pad = prefix if first else cont
            if pdfmetrics.stringWidth(pad + cur + ch, font_name, size) > max_w and cur:
                brk = cur.rfind(" ")
                if brk > len(cur) * 0.6:      # 공백 경계가 가까우면 거기서 접기
                    out.append(pad + cur[:brk])
                    cur = cur[brk + 1:] + ch
                else:
                    out.append(pad + cur)
                    cur = ch
                first = False
            else:
                cur += ch
        out.append((prefix if first else cont) + cur)
        return out

    for raw in text.split("\n"):
        for pline in _fit(raw):
            if y < 45:
                c.showPage(); c.setFont(font_name, size); y = H - 50
            c.drawString(x, y, pline)
            y -= 13
    c.save()
    return f"{title}.pdf", path
