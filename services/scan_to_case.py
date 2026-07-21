# -*- coding: utf-8 -*-
"""스캔 의무기록(scan_doc) → 심사 사건(application) 변환 — 결정적 매핑.

OCR로 적재된 검사 블록을 기존 심의의결서 파이프라인이 그대로 소비할 수 있게
application + disability + medical_record 행으로 변환한다.
변환 후에는 기존 흐름 그대로: AI 축 예측 → 판단내용 생성(LLM 서술) → 의결서 산출.

주의(HITL): 신분(duty_type)·분과·상이경위는 스캔 서류만으로 알 수 없어 기본값으로
채우고 표시해 둔다 — 담당자가 화면에서 확인·보완하는 것이 전제.
"""
import json
import re

import psycopg
from psycopg.rows import dict_row

from config.settings import PG_DSN

# 영문 판독 소견 → 상이처명 결정적 매핑 (자주 나오는 정형 패턴)
FINDING_MAP = [
    (r"ACL\s*reconstruction|anterior\s*cruciate", "무릎 전방십자인대 파열(재건술 후)", "다리"),
    (r"PCL|posterior\s*cruciate", "무릎 후방십자인대 파열", "다리"),
    (r"meniscus|meniscal", "무릎 반월상연골 파열", "다리"),
    (r"rotator\s*cuff", "어깨 회전근개 파열", "팔"),
    (r"achilles", "아킬레스건 파열", "다리"),
    (r"disc|HNP|herniation", "추간판탈출증", "척추"),
    (r"compression\s*fracture", "척추 압박골절", "척추"),
    (r"fracture", "골절", None),
]
SIDE_MAP = [(r"\bleft\b|\bLt\b", "좌"), (r"\bright\b|\bRt\b", "우"), (r"\bboth\b|양측", "양측")]


def _map_finding(txt):
    """판독 소견 문구에서 상이처명·부위·좌우 추출. 미매핑 시 원문 그대로."""
    name, part = None, None
    for pat, nm, pt in FINDING_MAP:
        if re.search(pat, txt, re.I):
            name, part = nm, pt
            break
    side = ""
    for pat, s in SIDE_MAP:
        if re.search(pat, txt, re.I):
            side = s
            break
    return name or txt.strip().rstrip("."), part, side


def _to_case_real(cur, conn, sd, blocks) -> dict:
    """실데이터 의무기록 묶음 → 사건 변환. 분과는 심의내용 키워드·KCD로 자동 라우팅."""
    from core.subcommittee import resolve

    recv_no = f"RD{sd['reg_no'] or sd['sd_id']}"
    cur.execute("SELECT app_id FROM application WHERE recv_no=%s", (recv_no,))
    dup = cur.fetchone()
    if dup:
        cur.execute("UPDATE scan_doc SET app_id=%s WHERE sd_id=%s", (dup["app_id"], sd["sd_id"]))
        conn.commit()
        return {"app_id": dup["app_id"], "existed": True, "relinked": True}

    disease = next((b["fields"].get("disease") for b in blocks if b["fields"].get("disease")), None) \
        or (sd["doc_kind"] or "").replace("의무기록 묶음(", "").rstrip(")") or "질병명 미상"
    kcd = next((b["fields"].get("kcd") for b in blocks if b["fields"].get("kcd")), None)
    full = sd.get("raw_text") or ""
    ctx = []  # 심의내용 문구 — 분과 라우팅 키워드 포함되게 조립
    if "고엽제" in full:
        ctx.append("고엽제후유(의)증")
    if "사망" in (sd.get("person") or "") or full.count("사망") > 3:
        ctx.append("상이사망")
    review_content = f"{disease} {' '.join(ctx)} 국가유공자 요건 해당 여부".strip()
    sub_no, _prof = resolve([kcd] if kcd else None, review_content)

    cur.execute(
        """INSERT INTO application(recv_no, applicant, birth_year, duty_type, is_death,
           review_content, subcommittee, status, apply_story, aftermath, apply_kind, is_real)
           VALUES (%s,%s,%s,%s,%s,%s,%s,'접수',%s,%s,'신규',true) RETURNING app_id""",
        (recv_no, sd["person"] or "성명미상",
         int(sd["reg_no"][:2]) + 1900 if sd.get("reg_no") else None,
         "병사", "상이사망" in ctx, review_content, sub_no,
         f"실데이터 의무기록 묶음({sd['hospital'] or '기관미상'}, 하위문서 {len(blocks)}건) OCR 적재분. "
         f"신분·발병 경위는 원문 대조 확인 필요.",
         f"주요 서식: {', '.join(dict.fromkeys(b['doc'] for b in blocks[:8]))}"))
    app_id = cur.fetchone()["app_id"]

    grade = next((b["fields"].get("grade") for b in blocks if b["fields"].get("grade")), None)
    cur.execute(
        """INSERT INTO disability(app_id, name, body_side, kcd_code, onset_ym,
           onset_story, fact_date, fact_place, fact_first_dx)
           VALUES (%s,%s,NULL,%s,%s,%s,NULL,NULL,%s) RETURNING dis_id""",
        (app_id, disease, kcd,
         next((b["fields"].get("date", "")[:7].replace("-", ".") for b in blocks
               if b["fields"].get("date")), None),
         f"실데이터 스캔 묶음 기반. 신체검사 소견 등급: {grade or '미기재'}", disease))
    dis_id = cur.fetchone()["dis_id"]

    for b in blocks:
        d = b["fields"].get("date")
        if d and not re.match(r"(19[4-9]\d|20[0-3]\d)-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$", d):
            d = None  # OCR 잡음 날짜 방어 (구버전 적재분 포함)
        cur.execute(
            """INSERT INTO medical_record(dis_id, hospital, rec_type, period, rec_date,
               chief, diagnosis, imaging, finding, chronic, surgery, by_applicant)
               VALUES (%s,%s,%s,NULL,%s,NULL,%s,NULL,%s,NULL,NULL,'N')""",
            (dis_id, sd["hospital"] or "보훈병원", b["doc"], d,
             b["fields"].get("disease"), b["excerpt"][:400]))

    cur.execute("UPDATE scan_doc SET app_id=%s WHERE sd_id=%s", (app_id, sd["sd_id"]))
    conn.commit()
    return {"app_id": app_id, "dis_id": dis_id, "dis_name": disease, "subcommittee": sub_no,
            "records": len(blocks), "is_real": True, "existed": False}


def to_grade(sd_id: int) -> dict:
    """실데이터 신검 서류 묶음 → 상이등급 안건(grade_agenda) 변환.
    (신체검사 의사 소견서·검진결과통보서 등은 요건심사가 아니라 등급심사 서류)"""
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM scan_doc WHERE sd_id=%s", (sd_id,))
        sd = cur.fetchone()
        if not sd:
            return {"error": "스캔 문서 없음"}
        if sd.get("ga_id"):
            return {"ga_id": sd["ga_id"], "existed": True}
        blocks = sd["exams"] or []
        if isinstance(blocks, str):
            blocks = json.loads(blocks)
        if not blocks:
            return {"error": "파싱된 하위문서가 없어 변환 불가"}

        agenda_no = f"RD{sd['reg_no'] or sd['sd_id']}호"
        cur.execute("SELECT ga_id FROM grade_agenda WHERE agenda_no=%s", (agenda_no,))
        dup = cur.fetchone()
        if dup:
            cur.execute("UPDATE scan_doc SET ga_id=%s WHERE sd_id=%s", (dup["ga_id"], sd_id))
            conn.commit()
            return {"ga_id": dup["ga_id"], "existed": True, "relinked": True}

        def first(key):
            # LLM 정규화 결과(norm) 우선, 없으면 규칙 추출 필드(fields)
            return next((v for b in blocks
                         for v in [(b.get("norm") or {}).get(key) or (b.get("fields") or {}).get(key)]
                         if v), None)

        disease = first("disease") or (sd["doc_kind"] or "").replace("의무기록 묶음(", "").rstrip(")") or "질병명 미상"
        grade = first("grade")
        exam_kind = first("exam_kind") or "재확인"
        exam_dept = first("exam_dept")
        opinion = first("opinion")
        full = sd.get("raw_text") or ""
        if "고엽제" in full:
            disease_ctx = f"{disease} (고엽제 후유의증)"
        else:
            disease_ctx = disease
        docs = list(dict.fromkeys(b["doc"] for b in blocks))

        cur.execute(
            """INSERT INTO grade_agenda(agenda_no, applicant, recv_no, apply_type, body_part,
               injury, base_date, exam_dept, grade_date, grade_change, ai_summary, status,
               review_items, note_items, progress, assignee, resident_no, target_type,
               yeu_injury, direct_review, exam_grade, specialist_opinion, related_docs,
               injury_items, is_real)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true)
               RETURNING ga_id""",
            (agenda_no, sd["person"] or "성명미상", f"RD{sd['reg_no'] or sd_id}",
             f"재심의({exam_kind})" if exam_kind in ("재심", "신규") else exam_kind,
             None, disease_ctx, first("date"), exam_dept, first("date"),
             f"{grade or '미기재'} → {exam_kind} 대상",
             f"실데이터 신검 서류 {len(blocks)}건 OCR 적재 — {disease_ctx} 등급 대조 필요", "미흡",
             [f"신검 서류 원문 대조 확인 필요 ({sd['hospital'] or '기관미상'})",
              f"등급및분류번호 {grade or '미기재'} — 별표3 기준 부합 여부 확인"],
             ["실데이터 — 개인정보 마스킹 적용분"], "자료수집", None,
             f"{sd['reg_no']}-*******" if sd.get("reg_no") else None,
             "고엽제후유의증" if "고엽제" in full else "공상군경",
             disease, None, grade, opinion, docs,
             json.dumps([{"injury": disease, "body_part": None, "prev_grade": grade,
                          "exam_dept": exam_dept, "exam_grade": grade, "opinion": opinion,
                          "review_items": None, "note_items": None, "related_docs": docs}],
                        ensure_ascii=False)))
        ga_id = cur.fetchone()["ga_id"]
        cur.execute("UPDATE scan_doc SET ga_id=%s WHERE sd_id=%s", (ga_id, sd_id))
        conn.commit()
        return {"ga_id": ga_id, "agenda_no": agenda_no, "injury": disease_ctx,
                "exam_grade": grade, "is_real": True, "existed": False}


def to_case(sd_id: int) -> dict:
    """scan_doc 1건을 application 사건으로 변환. 이미 변환된 경우 기존 app_id 반환."""
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM scan_doc WHERE sd_id=%s", (sd_id,))
        sd = cur.fetchone()
        if not sd:
            return {"error": "스캔 문서 없음"}
        if sd["app_id"]:
            return {"app_id": sd["app_id"], "existed": True}

        exams = sd["exams"] or []
        if isinstance(exams, str):
            exams = json.loads(exams)
        if not exams:
            return {"error": "파싱된 검사 블록이 없어 사건 변환 불가 — OCR 결과 확인 필요"}

        if sd.get("is_real"):
            return _to_case_real(cur, conn, sd, exams)

        # 대표 소견: 검사 블록에서 가장 자주 나온 Finding (없으면 Conclusion)
        counts = {}
        for e in exams:
            key = (e.get("finding") or e.get("conclusion") or "").strip()
            if key:
                counts[key] = counts.get(key, 0) + 1
        rep = max(counts, key=counts.get) if counts else (exams[0].get("dx") or "판독 소견 미상")
        dis_name, body_part, side = _map_finding(rep)

        first_exam = min((e.get("exam_date") or "" for e in exams), default="") or None
        onset_ym = first_exam[:7].replace("-", ".") if first_exam else None

        # 재적재 대비: 같은 등록번호로 이미 변환된 사건이 있으면 새로 만들지 않고 재연결
        recv_no = f"SC{sd['reg_no'] or sd_id}"
        cur.execute("SELECT app_id FROM application WHERE recv_no=%s", (recv_no,))
        dup = cur.fetchone()
        if dup:
            cur.execute("UPDATE scan_doc SET app_id=%s WHERE sd_id=%s", (dup["app_id"], sd_id))
            conn.commit()
            return {"app_id": dup["app_id"], "existed": True, "relinked": True}

        # ① application — 신분·분과는 서류만으로 미상: 기본값 + 담당자 확인 표시(HITL)
        cur.execute(
            """INSERT INTO application(recv_no, applicant, birth_year, duty_type, is_death,
               review_content, subcommittee, status, apply_story, aftermath, apply_kind)
               VALUES (%s,%s,%s,%s,false,%s,%s,'접수',%s,%s,'신규') RETURNING app_id""",
            (recv_no, sd["person"] or "성명미상", None, "병사",
             f"{dis_name} 국가유공자 요건 해당 여부",
             "2",
             f"스캔 의무기록({sd['doc_kind']}, {sd['hospital'] or '기관미상'}) OCR 기반 자동 생성 사건. "
             f"신분·분과·발병 경위는 담당자 확인 필요.",
             f"{sd['hospital'] or '병원'} 영상검사 {len(exams)}건 판독 완료 상태."))
        app_id = cur.fetchone()["app_id"]

        # ② disability
        cur.execute(
            """INSERT INTO disability(app_id, name, body_side, kcd_code, onset_ym,
               onset_story, fact_date, fact_place, fact_first_dx)
               VALUES (%s,%s,%s,NULL,%s,%s,NULL,NULL,%s) RETURNING dis_id""",
            (app_id, dis_name, side or None, onset_ym,
             f"스캔 서류 판독 소견 기반: {rep}",
             exams[0].get("dx")))
        dis_id = cur.fetchone()["dis_id"]

        # ③ medical_record — 검사 블록마다 1건
        for e in exams:
            finding = e.get("finding") or e.get("conclusion") or ""
            cur.execute(
                """INSERT INTO medical_record(dis_id, hospital, rec_type, period, rec_date,
                   chief, diagnosis, imaging, finding, chronic, surgery, by_applicant)
                   VALUES (%s,%s,'영상검사',NULL,%s,NULL,%s,%s,%s,NULL,%s,'N')""",
                (dis_id, sd["hospital"] or "보훈병원",
                 (e.get("exam_date") or "")[:10] or None,
                 e.get("dx"), e.get("exam_name"), finding,
                 "Y" if re.search(r"S/P|reconstruction|op\b", finding, re.I) else None))

        cur.execute("UPDATE scan_doc SET app_id=%s WHERE sd_id=%s", (app_id, sd_id))
        conn.commit()
        return {"app_id": app_id, "dis_id": dis_id, "dis_name": dis_name,
                "records": len(exams), "existed": False}
