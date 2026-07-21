# -*- coding: utf-8 -*-
"""누적 기능 회귀 테스트 — DB 없이 도는 순수 단위 위주 (DB 필요분은 자동 skip).

커버: OCR 실데이터 파싱 헬퍼 / 심사표 등급 로직 / 유사사례 선별 /
OCR 정규화 파서·폴백 / 판단문 자료 조립 / 판정기준 계산 체커 / 소견 매핑.
실행: python3 -m pytest tests/test_regression.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


# ── OCR 실데이터 파싱 (scripts/ocr_ingest_scans.py) ─────────────────
def test_valid_name_rejects_labels():
    from scripts.ocr_ingest_scans import _valid_name
    assert _valid_name("하정길") == "하정길"
    assert _valid_name("고.박병규") == "박병규"     # 故 접두 제거
    assert _valid_name("성별") is None              # 라벨 오추출 차단
    assert _valid_name("주민") is None
    assert _valid_name("가") is None                # 1자


def test_person_from_filename_nfd():
    """macOS 파일명은 NFD(자모 분해) — NFC 정규화 없이는 한글 정규식 미매칭."""
    import unicodedata
    from scripts.ocr_ingest_scans import person_from_filename
    nfd = unicodedata.normalize("NFD", "오인규(441029)_OCR_20260721.txt")
    assert person_from_filename(nfd) == "오인규"
    nfd2 = unicodedata.normalize("NFD", "제출서류(故권영락)_OCR.txt")
    assert person_from_filename(nfd2) == "권영락"
    nfd3 = unicodedata.normalize("NFD", "최승락88-근전도_OCR.txt")
    assert person_from_filename(nfd3) == "최승락"


def test_valid_disease_filters_labels():
    from scripts.ocr_ingest_scans import _valid_disease
    assert _valid_disease("폐암") == "폐암"
    assert _valid_disease("5. 당뇨병") == "당뇨병"   # 번호 머리 제거
    assert _valid_disease("신체검사 의사소견") is None


def test_parse_real_bundle_exam_kind_priority():
    """서식에 신규/재심 라벨이 항상 인쇄돼 있어 재확인·재판정을 우선 매칭해야 한다."""
    from scripts.ocr_ingest_scans import parse_real_bundle
    text = ("신체검사 의사 소견서\n- 신 규\n] 재 심\n신검종류\n재확인\n"
            "상이처(질병명)\n폐암\n등급및분류번호\n6급3항5110호\n2025년 12월 24일\n")
    blocks = parse_real_bundle(text)
    f = blocks[0]["fields"]
    assert f["exam_kind"] == "재확인"
    assert f["grade"] == "6급3항5110호"
    assert f["date"] == "2025-12-24"


def test_bundle_date_noise_rejected():
    from scripts.ocr_ingest_scans import parse_real_bundle
    blocks = parse_real_bundle("진단서\n2822년 60월 20일\n2006년 11월 30일\n")
    assert blocks[0]["fields"].get("date") == "2006-11-30"   # 잡음(2822-60-20) 배제


def test_rrn_masking_and_regno():
    """등록번호는 마스킹 전 원문에서 — 마스킹 후엔 \\b 경계 소실로 미매칭 (버그 회귀 방지)."""
    from scripts.ocr_ingest_scans import RE_RRN
    orig = "주민등록번호 440425-1110116 끝"
    m = RE_RRN.search(orig)
    assert m and m.group(1) == "440425"
    masked = RE_RRN.sub(lambda mm: f"{mm.group(1)}-{mm.group(2)[0]}******", orig)
    assert "1110116" not in masked and "440425-1******" in masked


# ── 심사표 등급 로직 (services/grade_export.py) ─────────────────────
def test_severity_formats():
    from services.grade_export import _severity
    assert _severity("6급1항 4113호") == 6
    assert _severity("3급") == 3
    assert _severity("6-1-5203") == 6      # 하이픈 표기
    assert _severity("등급기준미달") == 99
    assert _severity(None) == 99


def test_proposed_prefers_exam_grade():
    """상이처별 제안등급 = 신검등급 기반, AI 예측은 참고 (6급인데 2급 튀던 버그 회귀 방지)."""
    from services.grade_export import _proposed
    assert _proposed({"exam_grade": "6급1항 4113호"}, "2급 4105호") == "6급1항 4113호"
    assert _proposed({"exam_grade": None}, "5급 4206호") == "5급 4206호"  # 미기재 시만 AI


def test_total_grade_most_severe():
    from services.grade_export import _total_grade
    assert _total_grade(["7급 8122호", "3급 5104호", "등급기준미달"]) == "3급 5104호"
    assert _total_grade(["등급기준미달"]) is None


def test_updown_tags():
    from services.grade_export import _updown, _prev_grade
    assert _updown("등급기준미달", "3급 5104호") == "승급(기준미달→등급)"
    assert _updown("7급 8122호", "7급 8122호") == "직전등급 유지"
    assert _updown("6급2항", "7급") == "하향"
    assert _prev_grade({"grade_change": "7급 8122호 → 재심의 대상"}) == "7급 8122호"


# ── 유사사례 선별 (services/similar_pick.py) ────────────────────────
def test_apply_picks_exclude_pin_sort():
    from services.similar_pick import apply_picks
    items = [{"case_id": 1, "similarity": 0.9}, {"case_id": 2, "similarity": 0.8}]
    picks = [{"case_id": 1, "kind": "exclude", "weight": 1.0, "note": None},
             {"case_id": 9, "kind": "pin", "weight": 2.0, "note": "위원 제시"}]
    out = apply_picks(items, picks, "case_id",
                      fetch_pinned=lambda ids: [{"case_id": i} for i in ids])
    assert [x["case_id"] for x in out] == [9, 2]   # 제외 소거·고정 최우선
    assert out[0]["pick"] == "pin" and out[0]["pick_note"] == "위원 제시"


# ── OCR 정규화 (services/ocr_normalize.py) ──────────────────────────
def test_parse_json_tolerates_fences():
    from services.ocr_normalize import _parse_json
    assert _parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json('설명입니다 {"a": 2} 끝') == {"a": 2}
    assert _parse_json("JSON 아님") is None


def test_rule_norm_fallback_keeps_fields():
    from services.ocr_normalize import _rule_norm
    b = {"doc": "진단서", "fields": {"disease": "폐암", "grade": "6급3항5110호"}}
    n = _rule_norm(b, "상기 진단에 대해서 폐엽절제술 시행받음.\n조직검사 결과 확인됨.")
    assert n["source"] == "rule" and n["disease"] == "폐암"
    assert n["key_findings"]


# ── 판단문 자료 조립 (services/decision_doc._dossier — 순수 함수) ────
def test_dossier_includes_history_similar_precedents():
    from services.decision_doc import _dossier
    app = {"applicant": "박O철", "duty_type": "병사", "round": 2,
           "apply_story": "재신청 사건", "aftermath": None,
           "apply_history": [{"seq": 1, "date": "2021.03.10", "kind": "신규",
                              "summary": "최초 신청", "result": "비해당"}],
           "service": {}, "official_docs": []}
    d = {"name": "추간판탈출증", "body_side": "요추", "kcd_code": "M51.2",
         "onset_ym": "2020.05", "fact_date": None, "fact_place": None,
         "fact_first_dx": None, "dis_id": 1, "medical": [],
         "similar": [{"case_id": 7, "decision": "해당", "pick": "pin",
                      "summary": "포탄 적재 중 급성 요추손상"}],
         "precedents": [{"content": "급성 파열 소견이 확인되면 상당인과관계 인정"}]}
    txt = _dossier(app, d)
    assert "재신청 이력1" in txt and "비해당" in txt
    assert "★담당자 추가" in txt and "포탄 적재" in txt
    assert "관련 판례 발췌" in txt


# ── 판정기준 계산 체커 (services/rule_check.py) ─────────────────────
def test_mri_check_acute_vs_chronic():
    from services.rule_check import _mri_check, _parse_date
    assert _parse_date("2019.03.14.").isoformat() == "2019-03-14"
    dis = {"fact_date": "2019.03.14", "onset_ym": None}
    ok, _ = _mri_check(dis, [{"imaging": "MRI", "rec_type": "영상", "rec_date": "2019-03-28"}])
    assert ok == "ok"                                  # 14일 — 급성
    lack, _ = _mri_check(dis, [{"imaging": "MRI", "rec_type": "영상", "rec_date": "2020-01-05"}])
    assert lack == "lack"                              # 3개월 초과
    man, _ = _mri_check({"fact_date": None, "onset_ym": None}, [])
    assert man == "manual"                             # 자료 없음


# ── 소견→상이처 매핑 (services/scan_to_case.py) ─────────────────────
def test_map_finding():
    from services.scan_to_case import _map_finding
    name, part, side = _map_finding("S/P ACL reconstruction, left.")
    assert name == "무릎 전방십자인대 파열(재건술 후)" and side == "좌"


# ── DB 연동 (있을 때만 — 폐쇄망·CI 환경 대비 자동 skip) ─────────────
def _db_or_skip():
    import psycopg
    from config.settings import PG_DSN
    try:
        return psycopg.connect(PG_DSN, connect_timeout=2)
    except Exception:
        pytest.skip("PostgreSQL 미기동 — DB 회귀는 건너뜀")


def test_db_rule_graph_facts():
    conn = _db_or_skip()
    with conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM kg_nodes WHERE ntype='jr_disease'")
        if cur.fetchone()[0] == 0:
            pytest.skip("룰 그래프 미적재 — scripts/build_rule_graph.py 실행 필요")
    from core.graph import rule_facts
    facts = rule_facts("십자인대 파열 인정 기준이 뭐야?")
    assert facts and any("급성/진구성" in f for f in facts)
    assert any("필요서류" in f for f in facts)
