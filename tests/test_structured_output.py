# -*- coding: utf-8 -*-
"""구조화 출력·LLM 캐시·프롬프트 지문 회귀 — 에이전트 엔지니어링 개선(260730).

①구조화 출력: generate_json이 json_schema 강제 디코딩(FabrixClient)으로 가고,
  미지원 서빙(400)·Mock은 일반 생성+파싱 폴백으로 동일 결과를 내는지.
③LLM 캐시: 동일 프롬프트 재호출 시 HTTP 왕복 없이 캐시로 답하는지 (라이브 PG).
④프롬프트 버전: 템플릿 지문이 안정적이고 런 매니페스트에 실릴 형태인지.
"""
import uuid

import pytest

import config.settings as st
from core.llm_client import (FabrixClient, LLMClient, MockLLM,
                             _parse_json_strict, prompt_fingerprints)

SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}


class _Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code, self.text = data, status, str(data)

    def json(self):
        return self._d


def _flat_render(monkeypatch, text="프롬프트"):
    """템플릿 파일 의존 제거 — 렌더링이 아니라 호출·캐시 경로만 검증."""
    monkeypatch.setattr(LLMClient, "_render", lambda self, name, vars: text)


# ── ① 구조화 출력 ────────────────────────────────────────────────────────

def test_parse_json_strict_variants():
    assert _parse_json_strict('{"a": 1}') == {"a": 1}
    assert _parse_json_strict('```json\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json_strict('결과는 다음과 같습니다.\n{"a": 1}\n이상입니다.') == {"a": 1}
    with pytest.raises(ValueError):
        _parse_json_strict("JSON이 아닌 답변")


def test_mock_generate_json_canned(monkeypatch):
    """MockLLM도 canned 응답(코드펜스 포함)으로 generate_json 경로를 태울 수 있다."""
    _flat_render(monkeypatch)
    llm = MockLLM(canned={"foo": '```json\n{"a": "값"}\n```'})
    assert llm.generate_json("foo", SCHEMA) == {"a": "값"}
    with pytest.raises(ValueError):   # canned 없음 → "[MOCK] ..." → 파싱 실패 전파
        MockLLM().generate_json("bar", SCHEMA)


def test_fabrix_json_schema_payload(monkeypatch):
    """FabrixClient.generate_json이 response_format(json_schema)+모델 라우팅을 실어 보내는지."""
    _flat_render(monkeypatch)
    monkeypatch.setenv("LLM_CACHE", "0")
    sent = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.update(json)
        return _Resp({"choices": [{"message": {"content": '{"a": "b"}'}}], "usage": {}})

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    out = FabrixClient().generate_json("chatbot", SCHEMA)
    assert out == {"a": "b"}
    rf = sent["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["schema"] == SCHEMA and rf["json_schema"]["strict"] is True
    assert sent["model"] == FabrixClient._model_for("chatbot")


def test_fabrix_json_400_fallback(monkeypatch):
    """서빙이 json_schema 미지원(400)이면 일반 생성+파싱으로 폴백 — 결과는 동일."""
    _flat_render(monkeypatch)
    monkeypatch.setenv("LLM_CACHE", "0")
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append("json" if "response_format" in json else "plain")
        if "response_format" in json:
            return _Resp({"error": "response_format not supported"}, status=400)
        return _Resp({"choices": [{"message": {"content": '설명 후 {"a": "폴백"} 끝'}}],
                      "usage": {}})

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    assert FabrixClient().generate_json("judgment", SCHEMA) == {"a": "폴백"}
    assert calls == ["json", "plain"]


# ── ③ LLM 캐시 (라이브 PG — 폐쇄망 API 과금 절감) ──────────────────────────

def _pg_up() -> bool:
    try:
        import psycopg
        from config.settings import PG_DSN
        psycopg.connect(PG_DSN, connect_timeout=2).close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _pg_up(), reason="PostgreSQL 미기동")
def test_cache_hit_skips_http(monkeypatch):
    """동일 프롬프트 2회 → HTTP 1회. 프롬프트가 다르면 다시 호출."""
    unique = f"캐시검증-{uuid.uuid4().hex}"
    _flat_render(monkeypatch, unique)
    monkeypatch.setenv("LLM_CACHE", "1")
    n = {"post": 0}

    def fake_post(url, headers=None, json=None, timeout=None):
        n["post"] += 1
        return _Resp({"choices": [{"message": {"content": f"답변{n['post']}"}}], "usage": {}})

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    llm = FabrixClient()
    assert llm.generate("chatbot") == "답변1"
    assert llm.generate("chatbot") == "답변1"   # 캐시 히트 — HTTP 재호출 없음
    assert n["post"] == 1
    _flat_render(monkeypatch, unique + "-다른입력")
    assert llm.generate("chatbot") == "답변2"   # 내용이 다르면 키가 달라 재생성
    assert n["post"] == 2


@pytest.mark.skipif(not _pg_up(), reason="PostgreSQL 미기동")
def test_cache_json_and_text_keys_separate(monkeypatch):
    """generate와 generate_json은 같은 프롬프트라도 캐시 키가 분리된다(#json)."""
    unique = f"키분리-{uuid.uuid4().hex}"
    _flat_render(monkeypatch, unique)
    monkeypatch.setenv("LLM_CACHE", "1")
    n = {"post": 0}

    def fake_post(url, headers=None, json=None, timeout=None):
        n["post"] += 1
        return _Resp({"choices": [{"message": {"content": '{"a": "x"}'}}], "usage": {}})

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    llm = FabrixClient()
    llm.generate("chatbot")
    llm.generate_json("chatbot", SCHEMA)
    assert n["post"] == 2                       # 텍스트/JSON 별도 키
    assert llm.generate_json("chatbot", SCHEMA) == {"a": "x"}
    assert n["post"] == 2                       # JSON 캐시 히트


def test_mock_never_caches(monkeypatch):
    """Mock은 캐시 대상이 아니다 — 테스트 결정성은 Mock 자체가 보장."""
    from core import llm_client as lc
    monkeypatch.setenv("LLM_CACHE", "1")
    assert lc._cache_enabled(MockLLM()) is False
    assert lc._cache_enabled(FabrixClient()) is True
    monkeypatch.setenv("LLM_CACHE", "0")
    assert lc._cache_enabled(FabrixClient()) is False


# ── ④ 프롬프트 버전 지문 ──────────────────────────────────────────────────

def test_prompt_fingerprints_stable():
    fps = prompt_fingerprints()
    assert fps == prompt_fingerprints()                 # 결정적
    for name in ("judgment", "case_draft", "ocr_normalize", "file_notes", "chatbot"):
        assert name in fps and len(fps[name]) == 8
        int(fps[name], 16)                              # sha256 앞 8자리(hex)


def test_runlog_records_fingerprints(tmp_path):
    """런 매니페스트 _start 행에 프롬프트 지문이 실린다 — 산출물 재현 추적."""
    import json as _json
    from pipeline.runlog import RunLog
    log = RunLog("test", out_dir=str(tmp_path))
    first = _json.loads(log.path.read_text(encoding="utf-8").splitlines()[0])
    assert first["stage"] == "_start"
    assert first["prompt_fingerprints"].get("judgment") == prompt_fingerprints()["judgment"]
