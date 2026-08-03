# -*- coding: utf-8 -*-
"""API 명세 v0.2 계약 별칭 회귀 — ID는 v0.6 영문명·string 병행 (이름매핑 시트 계약)."""
import pytest

from api.main import _ID_ALIAS, _add_id_aliases


def test_alias_injection_nested():
    """중첩 dict/list 어디에 있어도 v0.6 키가 string으로 병행 수록된다."""
    data = {"app_id": 7, "disabilities": [{"dis_id": 3, "name": "무릎"}],
            "meta": {"case_id": 12}}
    _add_id_aliases(data)
    assert data["aply_log_sn"] == "7" and data["app_id"] == 7      # 원본 키 보존
    assert data["disabilities"][0]["wnd_sn"] == "3"
    assert data["meta"]["case_sn"] == "12"


def test_alias_no_overwrite_and_null():
    data = {"ga_id": None, "sd_id": 5, "orgtxt_dcmnt_sn": "이미있음"}
    _add_id_aliases(data)
    assert data["grd_srng_sn"] is None                             # null 유지
    assert data["orgtxt_dcmnt_sn"] == "이미있음"                    # 기존 값 미덮어씀


def test_request_models_accept_v06_names():
    """요청 바디는 프로토타입 키·v0.6 키 둘 다 수용, string 값도 int 변환."""
    from api.main import FeedbackReq, FinalizeReq, JudgeReq, SimilarPickReq
    r = JudgeReq.model_validate({"wnd_sn": "3", "yeu_result": "해당", "bosang_result": "비해당"})
    assert r.dis_id == 3
    assert JudgeReq.model_validate({"dis_id": 3, "yeu_result": "해당",
                                    "bosang_result": "해당"}).dis_id == 3
    assert FinalizeReq.model_validate({"wnd_sn": 4}).dis_id == 4
    p = SimilarPickReq.model_validate({"scope": "case", "trgt_case_sn": "9", "kind": "pin",
                                       "aply_log_sn": "2"})
    assert p.case_id == 9 and p.app_id == 2
    assert FeedbackReq.model_validate({"content": "글", "up_sn": 1}).parent_id == 1


def _pg_up():
    try:
        import psycopg
        from config.settings import PG_DSN
        psycopg.connect(PG_DSN, connect_timeout=2).close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _pg_up(), reason="PostgreSQL 미기동")
def test_cases_response_contract(monkeypatch):
    """실응답: 목록에 aply_log_sn(string) 병행 수록 + X-Total-Count 헤더 보존."""
    monkeypatch.setenv("EMBED_BACKEND", "hash")
    from fastapi.testclient import TestClient
    from api.main import app
    c = TestClient(app)
    r = c.get("/cases", params={"per_page": 2})
    rows = r.json()
    assert r.status_code == 200 and len(rows) <= 2
    if rows:
        assert rows[0]["aply_log_sn"] == str(rows[0]["app_id"])
        assert isinstance(rows[0]["aply_log_sn"], str)
    assert r.headers.get("x-total-count") is not None


@pytest.mark.skipif(not _pg_up(), reason="PostgreSQL 미기동")
def test_case_detail_and_save_all(monkeypatch):
    """명세 v0.3 신설 — 안건 상세(GET /cases/{id})·전체 저장(save-all)."""
    monkeypatch.setenv("EMBED_BACKEND", "hash")
    from fastapi.testclient import TestClient
    from api.main import app
    c = TestClient(app)
    r = c.get("/cases/1").json()
    assert r.get("aply_log_sn") == "1" and "disabilities" in r and r.get("step")
    if r["disabilities"]:
        assert r["disabilities"][0]["wnd_sn"] == str(r["disabilities"][0]["dis_id"])
    assert "error" in c.get("/cases/999999").json()
    ok = c.post("/case-draft/1/save-all",
                json={"sections": {"s1": "일괄 저장 검증"}}).json()
    assert ok["ok"] is True and ok["saved"] == ["s1"]
    bad = c.post("/case-draft/1/save-all", json={"sections": {"sx": "x"}}).json()
    assert bad["ok"] is False and "error" in bad


@pytest.mark.skipif(not _pg_up(), reason="PostgreSQL 미기동")
def test_right_panel_endpoints(monkeypatch):
    """우측 영역 — 안건 유사사례·작성이력·챗봇 안건 컨텍스트 (명세 v0.5)."""
    monkeypatch.setenv("EMBED_BACKEND", "hash")
    from fastapi.testclient import TestClient
    from api.main import app
    c = TestClient(app)
    sim = c.get("/cases/1/similar", params={"n": 3}).json()
    assert isinstance(sim, list) and len(sim) <= 3
    if sim:
        assert {"case_sn", "similarity", "summary", "reason"} <= set(sim[0])
    hist = c.get("/cases/1/history").json()
    assert isinstance(hist, list)
    if hist:
        assert {"at", "area", "event", "actor"} <= set(hist[0])
        assert hist[0]["actor"] in ("AI", "담당자")
    assert "error" in c.get("/cases/999999/similar").json()
