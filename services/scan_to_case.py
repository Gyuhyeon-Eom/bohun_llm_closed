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
