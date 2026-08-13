# -*- coding: utf-8 -*-
"""통합보훈(심사등록 NXMPVA) 원천 테이블 레지스트리 — 연계 수집·백필의 단일 참조.

근거: 통합보훈_심사등록_테이블(124개) 목록(260813 수령) 중 선별 44종
      (팀 표시 24 + 조인 무결성·기능 커버리지 보완 20).
용도: ① 수집 어댑터(flow_link._step_collect) 구현 시 대상 확인
      ② 사례풀 백필(과거 의결서 → cases) 원천 식별
      ③ DB 덤프 수령 시 컬럼 매핑 작성의 뼈대 — cols는 명세 수령 후 채운다.

주의: 전 테이블 개인정보 6호(실명·주민번호) 또는 4호(재판·수사) 등급 —
      반입·적재는 보안 절차 경유, 주민번호는 적재 전 마스킹.
"""

# 용도 구분: case=안건 수집 / verdict=의결·사례풀 백필 / exam=신체검사(등급)
#            req=요건 축 / file=문서·첨부 / master=코드·템플릿 마스터
SOURCE_TABLES = {
    # ── 안건·심의·의결 축 (verdict — 유사사례 풀·이전 심사기록 원천) ──
    "RV_AGND":            {"kr": "안건_TB_AA020", "use": "case", "to": "application(src_case_key)", "pri": 1},
    "RV_AGND_DLB":        {"kr": "안건심의", "use": "case", "to": "application", "pri": 1},
    "RV_AGND_HST":        {"kr": "안건이력_TB_AA020H", "use": "case", "to": "이전 심사기록", "pri": 2},
    "RV_AGND_SBMS":       {"kr": "안건상정_TB_AA022", "use": "case", "to": "회차 연결", "pri": 2},
    "RV_AGND_ST_HST":     {"kr": "안건상태이력_TB_ZZ902", "use": "case", "to": "진행상태", "pri": 2},
    "RV_DLB_DRFT":        {"kr": "심의문안_TB_AA023", "use": "verdict", "to": "cases(사례 요약·문안)", "pri": 1},
    "RV_WND_BY_DLB":      {"kr": "상이처별심의_TB_AA026", "use": "verdict", "to": "cases(상이처 판단)", "pri": 1},
    "RV_WND_BY_DLB_HST":  {"kr": "상이처별심의이력_TB_AA026H", "use": "verdict", "to": "이력", "pri": 2},
    "RV_VTN":             {"kr": "의결_TB_AA025", "use": "verdict", "to": "cases(의결 본체 — 주문·사유의 부모)", "pri": 1},
    "RV_VTN_HST":         {"kr": "의결이력_TB_AA025H", "use": "verdict", "to": "이력", "pri": 2},
    "RV_VTN_ODR":         {"kr": "의결주문", "use": "verdict", "to": "cases(decision)", "pri": 1},
    "RV_VTN_RSN":         {"kr": "의결사유", "use": "verdict", "to": "cases(사유문)", "pri": 1},
    "RV_VTN_XMN":         {"kr": "의결검토", "use": "verdict", "to": "cases(검토문)", "pri": 1},
    "RV_VTN_DRFT":        {"kr": "의결문안", "use": "verdict", "to": "cases(의결서 텍스트)", "pri": 1},
    "RV_DCS":             {"kr": "결정_TB_AA101", "use": "verdict", "to": "최종 처분", "pri": 1},
    "RV_DCS_DTL":         {"kr": "결정상세", "use": "verdict", "to": "최종 처분", "pri": 2},
    "RV_DCS_HST":         {"kr": "결정이력", "use": "verdict", "to": "이력", "pri": 2},
    "RV_MTNG":            {"kr": "회의_TB_AA021", "use": "verdict", "to": "회차(이전 심사기록 화면)", "pri": 2},
    "RV_MTNG_ATPR":       {"kr": "회의참석자", "use": "verdict", "to": "회차", "pri": 3},

    # ── 요건심사 요건 축 (req — 검토서 1~2장 근거) ──
    "RV_RQM_REQT":        {"kr": "요건의뢰_TB_AA001", "use": "req", "to": "application(접수)", "pri": 1},
    "RV_RQM_FACT":        {"kr": "요건사실_TB_AA010", "use": "req", "to": "apply_story(경위)", "pri": 1},
    "RV_RQM_FACT_HST":    {"kr": "요건사실이력", "use": "req", "to": "이력", "pri": 3},
    "RV_RQM_LDR":         {"kr": "요건원부_TB_AS501", "use": "req", "to": "application", "pri": 2},
    "RV_WD_RQM":          {"kr": "상이요건_TB_AA012", "use": "req", "to": "disability(상이처)", "pri": 1},
    "RV_WD_RQM_HST":      {"kr": "상이요건이력", "use": "req", "to": "이력", "pri": 3},
    "RV_MLRG_RQM":        {"kr": "병적요건_TB_AA013", "use": "req", "to": "service_record(병적 — 검토의견 7·14)", "pri": 1},
    "RV_MLRG_RQM_HST":    {"kr": "병적요건이력", "use": "req", "to": "이력", "pri": 3},
    "RV_SRV_RQM":         {"kr": "복무요건_TB_AA015", "use": "req", "to": "service_record", "pri": 1},
    "RV_PWR_RQM":         {"kr": "참전요건_TB_AA014", "use": "req", "to": "참전 유형", "pri": 2},
    "RV_DTH_RQM":         {"kr": "사망요건_TB_AA011", "use": "req", "to": "사망 유형", "pri": 2},
    "RV_OD3_MDCR":        {"kr": "3차진단서_TB_AA016", "use": "req", "to": "medical_record", "pri": 1},
    "RV_OD3_MDCR_DTL":    {"kr": "3차진단서상세", "use": "req", "to": "medical_record", "pri": 1},

    # ── 상이등급·신체검사 축 (exam) ──
    "RV_BDY_INSP_CMP":    {"kr": "신체검사종합_TB_AA051", "use": "exam", "to": "grade_agenda(신검)", "pri": 1},
    "RV_BDDT_BDY_INSP":   {"kr": "과목별신체검사_TB_AA052", "use": "exam", "to": "injury_items(신검과목)", "pri": 1},
    "RV_BDY_INSP_DTL":    {"kr": "신체검사상세_TB_AA055", "use": "exam", "to": "injury_items", "pri": 1},
    "RV_BDY_INSP_RTDC":   {"kr": "신체검사재판정_TB_AA053", "use": "exam", "to": "재판정 유형", "pri": 2},
    "RV_WND_BY_BDY_INSP": {"kr": "상이처별신체검사_TB_AA055", "use": "exam", "to": "injury_items", "pri": 1},
    "RV_WD_CMPX_NO":      {"kr": "상이복합호수_TB_AA103", "use": "exam", "to": "등급 호수(grade_case)", "pri": 1},
    "RV_WD_GD_CNC":       {"kr": "심사_상이등급연계", "use": "exam", "to": "요건↔등급 안건 연결", "pri": 1},

    # ── 문서·첨부 (file — 스캔 매핑·VLM 대상) ──
    "RV_ATFL":            {"kr": "첨부파일", "use": "file", "to": "scan_doc/case_file(원본 매핑)", "pri": 1},
    "RV_PVFL_FL":         {"kr": "자력철파일정보_TB_ZZ905", "use": "file", "to": "scan_doc(자력철)", "pri": 1},

    # ── 마스터·템플릿 (master) ──
    "RV_DLB_TYSG_AGD":    {"kr": "심의유형별의제", "use": "master", "to": "review_type/agenda", "pri": 1},
    "RV_DLB_TYSG_CTS":    {"kr": "심의유형별내용", "use": "master", "to": "review_content", "pri": 1},
    "RV_JDG_SE_RSN":      {"kr": "심사구분사유", "use": "master", "to": "심사구분 코드", "pri": 2},
    "RV_DRFT_TMPL_MNG":   {"kr": "문안템플릿관리", "use": "master", "to": "표준문안", "pri": 2},
    "RV_DRFT_TMPL_MNG_DTL": {"kr": "문안템플릿관리상세", "use": "master", "to": "표준문안 본문", "pri": 2},
}


def by_use(use: str) -> dict:
    return {k: v for k, v in SOURCE_TABLES.items() if v["use"] == use}


def summary() -> str:
    from collections import Counter
    c = Counter(v["use"] for v in SOURCE_TABLES.values())
    order = ["case", "req", "verdict", "exam", "file", "master"]
    return " / ".join(f"{u}:{c[u]}" for u in order if c[u]) + f" — 총 {len(SOURCE_TABLES)}종"
