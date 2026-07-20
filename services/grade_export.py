# -*- coding: utf-8 -*-
"""상이등급 심사표 xlsx 산출물 (가로형 — 원본 양식).
업로드된 실제 양식처럼 컬럼을 좌→우로 나열하고 안건을 행으로 표시.
단건(ga_id) 또는 전체(ga_id=None) export 지원. 작업로그는 별도 시트.
"""
import os
import tempfile


def _fetch(ga_id):
    import psycopg
    from psycopg.rows import dict_row
    from config.settings import PG_DSN
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        if ga_id:
            cur.execute("SELECT * FROM grade_agenda WHERE ga_id=%s", (ga_id,))
            ags = [cur.fetchone()]
        else:
            cur.execute("SELECT * FROM grade_agenda ORDER BY ga_id")
            ags = cur.fetchall()
        ags = [a for a in ags if a]
        logs_by = {}
        if ga_id and ags:
            cur.execute("SELECT step, event, actor, detail, file_name, status, created_at"
                        " FROM grade_log WHERE ga_id=%s ORDER BY gl_id", (ags[0]["ga_id"],))
            logs_by[ags[0]["ga_id"]] = cur.fetchall()
    return ags, logs_by


# 심사표 컬럼 (좌 → 우) — 확정 양식 12컬럼 (260720).
# "상이정도 및 소견"(__soken__)은 측정치·소견을 한 칸에 여러 줄로 합침(핵심 넓은 칸).
COLUMNS = [
    ("신검종류", "apply_type", 11),
    ("성명", "applicant", 9),
    ("주민등록번호", "resident_no", 15),
    ("대상구분", "target_type", 11),
    ("요건인정 상이처", "__yeu__", 22),
    ("직전등급\n신검과목", "__prev__", 15),
    ("신검등급", "exam_grade", 12),
    ("상이정도 및 소견\n(보훈병원 전문의)", "__soken__", 58),
    ("관련자료", "related_docs", 24),
    ("검토사항\n비고", "__review__", 34),
    ("상이처별 제안등급", "__grade_each__", 24),
    ("종합 제안등급", "__grade_total__", 14),
]


def _prev_grade(ag):
    """직전등급: grade_change('7급 8122호 → 재심의 대상')의 화살표 앞부분."""
    gc = ag.get("grade_change") or ""
    return gc.split("→")[0].strip() or "—"


def _predict_grade(ag, emb):
    """상이처별 AI 제안등급 — grade_predict(별표3 대조)로 산출. 실패 시 None."""
    if emb is None:
        return None
    try:
        from services import grade_predict
        r = grade_predict.predict(ag.get("yeu_injury") or ag.get("injury") or "",
                                  ag.get("body_part"), emb, n=3)
        return r.get("grade1")
    except Exception:
        return None


def export_xlsx(ga_id=None, emb=None):
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
    from openpyxl.utils import get_column_letter

    ags, logs_by = _fetch(ga_id)
    if not ags:
        wb = Workbook(); ws = wb.active; ws["A1"] = "안건을 찾을 수 없습니다."
        path = os.path.join(tempfile.gettempdir(), f"grade_{ga_id or 'all'}.xlsx"); wb.save(path)
        return f"grade_{ga_id or 'all'}.xlsx", path

    thin = Side(style="thin", color="888888")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    hdr_fill = PatternFill("solid", fgColor="D6E4D2")
    title_font = Font(name="맑은 고딕", size=14, bold=True)
    hf = Font(name="맑은 고딕", size=9.5, bold=True)
    cf = Font(name="맑은 고딕", size=9.5)
    wrap = Alignment(wrap_text=True, vertical="center", horizontal="center")
    wrap_l = Alignment(wrap_text=True, vertical="center", horizontal="left")
    center = Alignment(horizontal="center", vertical="center")

    wb = Workbook()
    ws = wb.active
    ws.title = "상이등급심사표"
    ncol = len(COLUMNS)

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncol)
    ws.cell(1, 1, "상 이 등 급 심 사 표").font = title_font
    ws.cell(1, 1).alignment = center
    ws.row_dimensions[1].height = 28
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncol)
    ws.cell(2, 1, "※ 시연용 표본 — 실제 사례 아님(개인정보 미포함)").font = Font(name="맑은 고딕", size=9, color="B45309")
    ws.cell(2, 1).alignment = center

    hr = 4
    for ci, (label, _, width) in enumerate(COLUMNS, 1):
        c = ws.cell(hr, ci, label)
        c.font = hf; c.fill = hdr_fill; c.border = border
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        ws.column_dimensions[get_column_letter(ci)].width = width
    ws.row_dimensions[hr].height = 40

    def fmt(v):
        if v is None:
            return "—"
        if isinstance(v, (list, tuple)):
            return "\n".join(f"○ {x}" for x in v) if v else "—"
        return str(v)

    def _measure_str(ag):
        import json as _j
        m = ag.get("measurements")
        if not m:
            return ""
        if isinstance(m, str):
            try: m = _j.loads(m)
            except Exception: return ""
        return " / ".join(f"{x.get('name')} {x.get('value')}{x.get('unit','')}"
                          + (f"({x.get('result')})" if x.get('result') else "") for x in m)

    def soken(ag):
        """상이정도 및 소견(보훈병원 전문의) — 측정치·소견 중심으로 한 칸에 여러 줄.
        (상이처·직전등급·검토사항은 별도 컬럼으로 분리 — 여기 중복 기재 안 함)"""
        L = []
        if ag.get("base_date") or ag.get("grade_date"):
            L.append(f"○ 기준일자 {ag.get('base_date') or '—'} / 등급기준일 {ag.get('grade_date') or '—'}")
        ms = _measure_str(ag)
        if ms:
            L.append(f"○ 신체검사 측정치: {ms}")
        if ag.get("specialist_opinion"):
            L.append(f"○ 전문의 소견: {ag['specialist_opinion']}")
        if ag.get("onset_narrative"):
            L.append(f"○ 발생경위: {ag['onset_narrative']}")
        if ag.get("prior_history"):
            L.append(f"○ 이전 판정·재심의 경위: {ag['prior_history']}")
        if ag.get("past_history"):
            L.append(f"○ 과거력·기왕증: {ag['past_history']}")
        if ag.get("route_note"):
            L.append(f"○ 경로사항: {ag['route_note']}")
        return "\n".join(L) or "—"

    for ri, ag in enumerate(ags, hr + 1):
        # AI 제안등급: 상이처별 = 별표3 대조 예측, 종합 = 상이처별 종합(현재 안건당 상이처 1건)
        g1 = _predict_grade(ag, emb)
        injury_label = ag.get("yeu_injury") or ag.get("injury") or "—"
        for ci, (label, key, _) in enumerate(COLUMNS, 1):
            if key == "__yeu__":
                val = injury_label
            elif key == "__prev__":
                val = f"{_prev_grade(ag)}\n{ag.get('exam_dept') or '—'}"
            elif key == "__soken__":
                val = soken(ag)
            elif key == "__review__":
                L = [f"○ {x}" for x in (ag.get("review_items") or [])]
                L += [f"◇ {x}" for x in (ag.get("note_items") or [])]
                val = "\n".join(L) or "—"
            elif key == "__grade_each__":
                val = f"· {injury_label}: {g1}" if g1 else "— (AI 예측 미실행)"
            elif key == "__grade_total__":
                val = g1 or "—"
            elif key == "related_docs" and ag.get(key):
                val = "\n".join(f"· {x}" for x in ag[key])
            else:
                val = fmt(ag.get(key))
            c = ws.cell(ri, ci, val)
            c.font = cf; c.border = border
            c.alignment = wrap_l if key in ("__soken__", "__yeu__", "__review__",
                "__grade_each__", "related_docs") else wrap
        # 소견 칸 줄 수에 맞춰 행 높이 (넓은 칸이 세로로 길어짐)
        soken_lines = soken(ag).count("\n") + 1
        # 셀 폭 68 기준 대략 줄바꿈 추정
        wrapped = sum(max(1, len(seg) // 60 + 1) for seg in soken(ag).split("\n"))
        ws.row_dimensions[ri].height = max(60, min(wrapped, 30) * 14)

    ws.freeze_panes = "A5"

    if ga_id and logs_by.get(ags[0]["ga_id"]):
        ws2 = wb.create_sheet("작업로그")
        heads = ["시각", "단계", "이벤트", "수행자", "상세", "첨부파일", "상태"]
        for ci, h in enumerate(heads, 1):
            c = ws2.cell(1, ci, h); c.font = hf; c.fill = hdr_fill; c.border = border; c.alignment = center
        for ri, lg in enumerate(logs_by[ags[0]["ga_id"]], 2):
            vals = [
                lg["created_at"].strftime("%Y-%m-%d %H:%M") if lg.get("created_at") else "",
                lg.get("step") or "", lg.get("event") or "", lg.get("actor") or "",
                lg.get("detail") or "", lg.get("file_name") or "", lg.get("status") or "",
            ]
            for ci, v in enumerate(vals, 1):
                c = ws2.cell(ri, ci, v); c.font = cf; c.border = border
                c.alignment = Alignment(wrap_text=True, vertical="top")
        for col, w in zip("ABCDEFG", (16, 10, 20, 10, 36, 14, 8)):
            ws2.column_dimensions[col].width = w
        ws2.freeze_panes = "A2"

    # 신체검사 측정치 시트 (단건, measurements 있을 때)
    if ga_id and ags[0].get("measurements"):
        import json as _json
        meas = ags[0]["measurements"]
        if isinstance(meas, str):
            meas = _json.loads(meas)
        ws3 = wb.create_sheet("신체검사 측정치")
        mheads = ["검사항목", "측정값", "단위", "기준", "판정"]
        for ci, h in enumerate(mheads, 1):
            c = ws3.cell(1, ci, h); c.font = hf; c.fill = hdr_fill; c.border = border; c.alignment = center
        for ri, m in enumerate(meas, 2):
            vals = [m.get("name", ""), str(m.get("value", "")), m.get("unit", ""),
                    m.get("ref", ""), m.get("result", "")]
            for ci, v in enumerate(vals, 1):
                c = ws3.cell(ri, ci, v); c.font = cf; c.border = border
                c.alignment = center if ci in (2, 3) else Alignment(wrap_text=True, vertical="center")
        for col, w in zip("ABCDE", (30, 16, 8, 20, 12)):
            ws3.column_dimensions[col].width = w
        ws3.freeze_panes = "A2"

    # 의무기록 시간순 시트 (단건, med_timeline 있을 때)
    if ga_id and ags[0].get("med_timeline"):
        import json as _json2
        tl = ags[0]["med_timeline"]
        if isinstance(tl, str):
            tl = _json2.loads(tl)
        ws4 = wb.create_sheet("의무기록 시간순")
        theads = ["진료일", "의료기관", "기록유형", "진단명", "소견"]
        for ci, h in enumerate(theads, 1):
            c = ws4.cell(1, ci, h); c.font = hf; c.fill = hdr_fill; c.border = border; c.alignment = center
        for ri, r in enumerate(tl, 2):
            vals = [r.get("date", ""), r.get("hospital", ""), r.get("type", ""),
                    r.get("dx", ""), r.get("finding", "")]
            for ci, v in enumerate(vals, 1):
                c = ws4.cell(ri, ci, v); c.font = cf; c.border = border
                c.alignment = Alignment(wrap_text=True, vertical="top")
            ws4.row_dimensions[ri].height = 44
        for col, w in zip("ABCDE", (14, 20, 14, 26, 60)):
            ws4.column_dimensions[col].width = w
        ws4.freeze_panes = "A2"

    if ga_id:
        ag = ags[0]
        safe = (ag.get("agenda_no") or f"ga{ga_id}").replace("/", "_")
        fname = f"상이등급심사표_{safe}_{ag.get('applicant','')}.xlsx"
    else:
        fname = "상이등급심사표_전체.xlsx"
    path = os.path.join(tempfile.gettempdir(), fname)
    wb.save(path)
    return fname, path
