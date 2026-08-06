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
    env = c.get("/cases/1/similar", params={"n": 3}).json()
    assert env["success"] is True and "message" in env      # v0.7 봉투 규약
    sim = env["data"]
    assert isinstance(sim, list) and len(sim) <= 3
    if sim:
        assert {"case_sn", "similarity", "summary", "reason"} <= set(sim[0])
    hist = c.get("/cases/1/history").json()
    assert isinstance(hist, list)
    if hist:
        assert {"at", "area", "event", "actor"} <= set(hist[0])
        assert hist[0]["actor"] in ("AI", "담당자")
    bad = c.get("/cases/999999/similar").json()
    assert bad["success"] is False and bad["message"]


@pytest.mark.skipif(not _pg_up(), reason="PostgreSQL 미기동")
def test_llm_server_minimal_requests(monkeypatch):
    """명세 v0.6 — 자바 백단 최소 요청: 종합판단 Y/N, 판정예측 ID만."""
    monkeypatch.setenv("EMBED_BACKEND", "hash")
    import psycopg
    from config.settings import PG_DSN
    from fastapi.testclient import TestClient
    from api.main import app
    c = TestClient(app)
    r = c.post("/decision-doc/1/judge",
               json={"wnd_sn": "1", "yeu_result": "Y", "bosang_result": "N"}).json()
    assert r.get("yeu_result") == "해당" and r.get("bosang_result") == "비해당"
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT ga_id FROM grade_agenda ORDER BY ga_id LIMIT 1")
        row = cur.fetchone()
    if row:
        p = c.post("/grade-predict", json={"grd_srng_sn": str(row[0])}).json()
        assert "error" not in p or p.get("grade1") is not None or "상이처가 없음" in p["error"]
    assert "error" in c.post("/grade-predict", json={"n": 3}).json()


@pytest.mark.skipif(not _pg_up(), reason="PostgreSQL 미기동")
def test_envelope_and_full_draft(monkeypatch):
    """v0.7 — 계약 API 봉투(success/message/data)·전체 초안 생성·비계약 경로 무봉투."""
    monkeypatch.setenv("EMBED_BACKEND", "hash")
    from fastapi.testclient import TestClient
    from api.main import app
    c = TestClient(app)
    d = c.post("/decision-doc/1/draft").json()
    assert d["success"] is True and len(d["data"]["sections"]) == 3
    j = c.post("/decision-doc/1/judge",
               json={"wnd_sn": "1", "yeu_result": "Y", "bosang_result": "N"}).json()
    assert j["success"] is True and j["data"]["yeu_result"] == "해당"
    assert j["yeu_result"] == "해당"            # 최상위 병행 키 (구화면 호환)
    assert isinstance(c.get("/cases").json(), list)   # 비계약 경로는 봉투 없음


@pytest.mark.skipif(not _pg_up(), reason="PostgreSQL 미기동")
def test_v09_grade_draft_and_files(monkeypatch):
    """v0.9 — 상이등급 심의의결서 생성·요건 초안 files[]·종합판단 wnd_sn 생략."""
    monkeypatch.setenv("EMBED_BACKEND", "hash")
    import psycopg
    from config.settings import PG_DSN
    from fastapi.testclient import TestClient
    from api.main import app
    c = TestClient(app)
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT ga_id FROM grade_agenda ORDER BY ga_id LIMIT 1")
        ga = cur.fetchone()[0]
    g = c.post(f"/grade-agendas/{ga}/draft").json()
    assert g["success"] is True and g["data"]["items"]
    assert {"injury", "proposed_grade", "opinion"} <= set(g["data"]["items"][0])
    d = c.post("/decision-doc/1/draft").json()
    s0 = d["data"]["sections"][0]
    assert "files" in s0 and {"file_id", "filename", "page_no", "summary"} <= set(s0["files"][0])
    j = c.post("/decision-doc/1/judge", json={"yeu_result": "Y", "bosang_result": "N"}).json()
    assert j["success"] is True and j["data"]["yeu_result"] == "해당"


@pytest.mark.skipif(not _pg_up(), reason="PostgreSQL 미기동")
def test_v10_export_envelope(monkeypatch):
    """v0.10 — 심사표 산출: 봉투 JSON(file_name/url), url은 xlsx 스트림, grade_log 보관 기록."""
    monkeypatch.setenv("EMBED_BACKEND", "hash")
    import psycopg
    from config.settings import PG_DSN
    from fastapi.testclient import TestClient
    from api.main import app
    c = TestClient(app)
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT ga_id FROM grade_agenda ORDER BY ga_id LIMIT 1")
        ga = cur.fetchone()[0]
    g = c.get(f"/grade-agendas/{ga}/export").json()
    assert g["success"] is True
    assert {"file_name", "url", "expires_s"} <= set(g["data"])
    assert g["data"]["file_name"].endswith(".xlsx")
    r2 = c.get(g["data"]["url"])                     # local이면 ?dl=1 스트림
    assert r2.status_code == 200
    assert r2.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml")
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT file_name FROM grade_log WHERE ga_id=%s AND event='심사표 엑셀 산출'"
                    " ORDER BY gl_id DESC LIMIT 1", (ga,))
        assert cur.fetchone()[0] == g["data"]["file_name"]
    bad = c.get("/grade-agendas/999999/export").json()
    assert bad["success"] is False and bad["message"]
