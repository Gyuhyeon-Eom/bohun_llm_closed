# -*- coding: utf-8 -*-
"""통합보훈(심사등록 NXMPVA) 수집 컬렉터 — 컬럼 명세(29종, 260813) 기반 실매핑.

원칙:
  - SQL은 표준 문법만 사용 — 원천(Oracle)과 덤프 스테이징(PostgreSQL) 어느 쪽에서도 동작.
    바인드는 DB-API paramstyle 차이를 피하려고 안전한 리터럴 치환(_q)로 처리(키는 영숫자 검증).
  - 매핑 함수는 순수 함수(rows → dict) — 접속 없이 단위검증 가능.
  - 개인정보: 주민번호(RRN)류 컬럼은 조회 자체를 하지 않는다. 성명·생년은 수집 후
    내부 정책(마스킹·접근통제)을 따른다.

확인 필요(명세만으로 미확정 — 덤프 수령 시 검증):
  - RV_WD_RQM/RV_SRV_RQM의 RQM_MNO(요건관리번호) ↔ RV_AGND(AGND_NO) 연결 경로.
    RV_AGND.RCNO(접수번호) 경유로 추정 — 미확인이라 기본은 RCNO 경유, 상수로 교체 가능.
  - 첨부 실파일 반출 방식(RV_ATFL은 파일명 메타만 보유).
"""
import re

# ── 안전한 파라미터 치환 (표준 SQL 유지 — 값은 영숫자·하이픈만 허용) ────────
_KEY_OK = re.compile(r"^[A-Za-z0-9_\-]+$")


def _q(sql: str, **params) -> str:
    out = sql
    for k, v in params.items():
        v = str(v)
        if not _KEY_OK.match(v):
            raise ValueError(f"허용되지 않는 키 값: {k}={v!r}")
        out = out.replace(f":{k}", f"'{v}'")
    return out


# ── 폴링: 신규 안건번호 (LINK_SRC_QUERY 미설정 시 기본) ─────────────────────
# 임시저장·삭제 제외. 증분 조건(DLB_REQT_DT 등)은 운영 협의 후 WHERE 보강.
Q_POLL_DEFAULT = ("SELECT AGND_NO FROM RV_AGND"
                  " WHERE DEL_YN = 'N' AND AGND_TMP_SV_YN = 'N'"
                  " ORDER BY AGND_NO")

# 요건관리번호 연결 경로: 'rcno' = RV_AGND.RCNO 경유(추정 기본) / 'agnd' = AGND_NO 직결
RQM_LINK_MODE = "rcno"


def _rows(cur, sql):
    cur.execute(sql)
    cols = [d[0].lower() for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def fetch_case(cur, agnd_no: str) -> dict:
    """안건 1건의 수집 번들 — 안건·심의·상이처별심의·의결사유·검토·요건(상이·복무)."""
    b = {"agnd_no": agnd_no}
    b["agnd"] = _rows(cur, _q(
        "SELECT AGND_NO, RCNO, AGND_KD_CD, AGND_SE_CD, JDG_SE_CD, JDG_TY_CD,"
        " DLB_TY_CD, DLB_CTS_CD, DLB_ST_CD, DLB_REQT_DT, BFR_AGND_NO, ATCH_FLID,"
        " MSC_BLN_CD, SBCM_CMMT_CD, INJR_DSS_CD, RTE_YN,"
        " GNR_AGND_MAIN_CTS, GNR_AGND_REFR_CTS, GNR_AGND_SGGS_RESN_CTS,"
        " GNR_AGND_VTN_ODR_CTS, ISS_AGND_ASSR_CTS, ISS_AGND_XMN_OPIN"
        " FROM RV_AGND WHERE AGND_NO = :no AND DEL_YN = 'N'", no=agnd_no))
    b["dlb"] = _rows(cur, _q(
        "SELECT DLB_JGD_CD, DLB_REQT_CIR_CTS, XMN_OPIN_CTS, SGGS_ODR_CTS,"
        " SGGS_RESN_CTS, RN_DAT_CTS, LW_APL_CD, BAS_LWO_REFR_MTR_CTS"
        " FROM RV_AGND_DLB WHERE AGND_NO = :no AND DEL_YN = 'N'", no=agnd_no))
    b["wnd_dlb"] = _rows(cur, _q(
        "SELECT DSNM_SN, WND_BY_DLB_SE_CD, RCG_WND_CTS, LW_APL_CD,"
        " RCG_SR_CRSD_RSN_CD, RCG_SR_NN_CRSD_RSN_CD"
        " FROM RV_WND_BY_DLB WHERE AGND_NO = :no AND DEL_YN = 'N'", no=agnd_no))
    b["vtn_rsn"] = _rows(cur, _q(
        "SELECT VTN_RSN_SN, VTN_RSN_CTS FROM RV_VTN_RSN"
        " WHERE AGND_NO = :no AND DEL_YN = 'N' ORDER BY VTN_RSN_SN", no=agnd_no))
    b["vtn_xmn"] = _rows(cur, _q(
        "SELECT DLB_JGD_CD, VTN_XMN_OPIN_CTS FROM RV_VTN_XMN"
        " WHERE AGND_NO = :no AND DEL_YN = 'N'", no=agnd_no))
    # 요건(상이·복무): RQM_MNO 연결 — 기본은 접수번호(RCNO) 경유(확인 필요 사항)
    link = agnd_no if RQM_LINK_MODE == "agnd" else (b["agnd"][0]["rcno"] if b["agnd"] else "")
    if link:
        b["wd_rqm"] = _rows(cur, _q(
            "SELECT WD_SN, ORG_WD_DSNM, NOW_WD_DSNM, WD_DT, WD_PLAC_NM,"
            " WD_CAUS_CTS, WD_MN_TIM_BLN_NM FROM RV_WD_RQM"
            " WHERE RQM_MNO = :mno AND DEL_YN = 'N' ORDER BY WD_SN", mno=link))
        b["srv_rqm"] = _rows(cur, _q(
            "SELECT SRV_SN, BLN_MRN_NM, SRV_DTS_NM, SRV_AR_NM,"
            " SRV_TR_BGN_DT, SRV_TR_ED_DT FROM RV_SRV_RQM"
            " WHERE RQM_MNO = :mno AND DEL_YN = 'N' ORDER BY SRV_SN", mno=link))
    else:
        b["wd_rqm"], b["srv_rqm"] = [], []
    return b


# ── 내부 스키마 매핑 (순수 함수) ─────────────────────────────────────────────

def to_application(b: dict) -> dict:
    """RV_AGND → application 필드. 코드값(심사구분 등)은 공통코드 미수령 상태라
    원값 저장 — 공통코드 테이블 수령 시 명칭 치환."""
    a = (b.get("agnd") or [{}])[0]
    story = "\n".join(filter(None, [
        a.get("gnr_agnd_main_cts"),
        next((d.get("dlb_reqt_cir_cts") for d in b.get("dlb", []) if d.get("dlb_reqt_cir_cts")), None),
    ]))
    return {
        "src_case_key": b["agnd_no"],
        "recv_no": a.get("rcno"),
        "review_content": a.get("dlb_cts_cd"),
        "subcommittee": a.get("sbcm_cmmt_cd"),
        "status": a.get("dlb_st_cd") or "접수",
        "apply_story": story or None,
        "duty_type": a.get("msc_bln_cd"),
        "agenda_no": b["agnd_no"],
        "prev_case_key": a.get("bfr_agnd_no"),   # 재신청·이의신청 연결(검토의견 18)
        "심사구분코드": a.get("jdg_se_cd"), "심의유형코드": a.get("dlb_ty_cd"),
        "심의의뢰일자": a.get("dlb_reqt_dt"),
    }


def to_disabilities(b: dict) -> list[dict]:
    """RV_WD_RQM → disability 행들 (원/현재 병명, 상이 경위)."""
    out = []
    for w in b.get("wd_rqm", []):
        out.append({
            "name": w.get("now_wd_dsnm") or w.get("org_wd_dsnm"),
            "org_name": w.get("org_wd_dsnm"),
            "onset_ym": (w.get("wd_dt") or "")[:6] or None,
            "fact_date": w.get("wd_dt"),
            "fact_place": w.get("wd_plac_nm"),
            "onset_story": "\n".join(filter(None, [
                w.get("wd_caus_cts"),
                f"당시 소속: {w['wd_mn_tim_bln_nm']}" if w.get("wd_mn_tim_bln_nm") else None])) or None,
        })
    return out


def to_service_records(b: dict) -> list[dict]:
    """RV_SRV_RQM → service_record 행들 (소속·직책·복무기간)."""
    return [{
        "unit": s.get("bln_mrn_nm"), "duty": s.get("srv_dts_nm"),
        "area": s.get("srv_ar_nm"),
        "begin": s.get("srv_tr_bgn_dt"), "end": s.get("srv_tr_ed_dt"),
    } for s in b.get("srv_rqm", [])]


def build_case_summary(b: dict, limit: int = 3500) -> dict:
    """사례풀(cases) 백필용 텍스트 조립 — 경위·쟁점·상이처별 심의·검토·주문·사유 순.
    유사사례 검색·문안 참조의 원천이므로 판단 서술을 최대한 보존한다."""
    a = (b.get("agnd") or [{}])[0]
    parts = []
    if a.get("gnr_agnd_main_cts"):
        parts.append(f"[주요내용] {a['gnr_agnd_main_cts']}")
    for d in b.get("dlb", []):
        if d.get("dlb_reqt_cir_cts"):
            parts.append(f"[심의의뢰 경위] {d['dlb_reqt_cir_cts']}")
        if d.get("xmn_opin_cts"):
            parts.append(f"[검토의견] {d['xmn_opin_cts']}")
    if a.get("iss_agnd_assr_cts"):
        parts.append(f"[쟁점 주장] {a['iss_agnd_assr_cts']}")
    if a.get("iss_agnd_xmn_opin"):
        parts.append(f"[쟁점 검토] {a['iss_agnd_xmn_opin']}")
    for w in b.get("wnd_dlb", []):
        if w.get("rcg_wnd_cts"):
            parts.append(f"[인정상이처] {w['rcg_wnd_cts']}")
    odr = a.get("gnr_agnd_vtn_odr_cts") or next(
        (d.get("sggs_odr_cts") for d in b.get("dlb", []) if d.get("sggs_odr_cts")), None)
    if odr:
        parts.append(f"[의결주문] {odr}")
    for r in b.get("vtn_rsn", []):
        if r.get("vtn_rsn_cts"):
            parts.append(f"[의결사유] {r['vtn_rsn_cts']}")
    summary = "\n".join(parts)[:limit]
    return {
        "review_type": a.get("dlb_ty_cd"),
        "review_content": a.get("dlb_cts_cd"),
        "exam_category": a.get("jdg_se_cd"),
        "decision": (odr or "")[:200] or None,
        "decided_at": None,   # 의결일자는 RV_VTN(미수령 — 본체 요청분) 확보 시 채움
        "summary": summary,
        "duty_type": a.get("msc_bln_cd"),
        "src_case_key": b["agnd_no"],
    }
