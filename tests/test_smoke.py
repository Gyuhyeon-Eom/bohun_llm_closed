"""DB·모델 없이 도는 스모크 테스트: 청킹 규칙 + 통계 SQL 가드."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingestion.ocr_adapter import MockOCR
from ingestion.chunker import chunk_blocks
from ingestion.types import BlockType
from services.stats import _is_safe, _extract_sql
from ingestion.verifier import verify_blocks
from ingestion.types import Block
from core.llm_client import MockLLM, LLMClient, LLMUnavailable, LLMTransient
from core.retrieval import _or_tsquery
from core.subcommittee import resolve, manual_doctype, profile_for


def test_chunking():
    chunks = chunk_blocks(MockOCR().extract("dummy.pdf"))
    assert chunks, "청크가 비어있음"
    tables = [c for c in chunks if c.block_type == BlockType.TABLE]
    assert len(tables) == 1, "표는 통째로 1청크여야 함"
    assert all(len(c.content) <= 1200 + 1 for c in chunks)
    print("PASS test_chunking:", len(chunks), "chunks")


def test_sql_guard():
    # 허용: 단일 SELECT, 화이트리스트 뷰
    assert _is_safe("SELECT * FROM v_stats_by_review_type")
    assert _is_safe("SELECT count(*) FROM v_stats_by_year WHERE EXTRACT(YEAR FROM decided_at)=2025")
    # 차단: 쓰기·화이트리스트 밖 단일 테이블
    assert not _is_safe("DELETE FROM cases")
    assert not _is_safe("SELECT * FROM cases")
    # 차단: 콤마 조인으로 화이트리스트 밖 테이블 끌어오기(정규식 가드가 놓치던 우회)
    assert not _is_safe("SELECT a.applicant FROM v_stats_by_year, application a")
    assert not _is_safe("SELECT * FROM v_stats_by_year, medical_record")
    # 차단: 서브쿼리·CTE로 밖 테이블 참조
    assert not _is_safe("SELECT * FROM v_stats_by_year WHERE 1 IN (SELECT app_id FROM application)")
    assert not _is_safe("WITH x AS (SELECT * FROM application) SELECT * FROM x")
    # 차단: DoS/부작용 함수
    assert not _is_safe("SELECT pg_sleep(30) FROM v_stats_by_year")
    # 차단: 다중 statement
    assert not _is_safe("SELECT 1 FROM v_stats_by_year; DROP TABLE cases")
    print("PASS test_sql_guard")


def test_extract_sql_fence():
    # LLM이 ```sql 펜스로 감싸 반환해도 실제 SQL만 추출
    assert _extract_sql("```sql\nSELECT 1 FROM v_stats_by_year;\n```") == "SELECT 1 FROM v_stats_by_year"
    assert _extract_sql("SELECT 1 FROM v_stats_by_year") == "SELECT 1 FROM v_stats_by_year"
    # 펜스 안 화이트리스트 밖 우회도 파서가 잡아야 함
    assert not _is_safe(_extract_sql("```sql\nSELECT * FROM application\n```"))
    print("PASS test_extract_sql_fence")


def test_tsquery_hyphen():
    # 하이픈만으로 된 토큰이 제거되어 to_tsquery 문법 오류를 일으키지 않아야 함
    q = _or_tsquery("예우법 - 4조")
    assert "-" not in q.split(" | "), f"하이픈 토큰 잔존: {q}"
    assert "예우법" in q and "4조" in q
    assert _or_tsquery("---") == "___none___"   # 유효 토큰 없으면 안전 플레이스홀더
    print("PASS test_tsquery_hyphen")


def test_subcommittee_routing():
    # "고엽제"만 있으면 4분과(과거 버그: 조건 무관 4로 오배정 → 지금은 명시적 4가 맞음)
    assert resolve([], "고엽제후유의증")[0] == "4"
    # "고엽제"+"자해"는 자해가 우선해 5분과로 가야 함(과거엔 5에 도달 못 함)
    assert resolve([], "고엽제 자해 관련")[0] == "5"
    # 범위 밖/미상 분과도 KeyError 없이 폴백
    assert profile_for(9) is not None
    assert profile_for(None) is not None
    assert manual_doctype(9) is None
    print("PASS test_subcommittee_routing")


class _FlakyLLM(LLMClient):
    """n회 실패 후 성공. 재시도 로직 검증용."""
    def __init__(self, exc, fail_times):
        self.exc, self.fail_times, self.calls = exc, fail_times, 0
    def _call(self, prompt):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc
        return "OK"


def test_retry_logic():
    import os
    prompts = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "core", "prompts")
    # LLM_MAX_RETRIES=2 → 최대 3회 시도. 일시 오류(Transient)는 재시도해 성공.
    t = _FlakyLLM(LLMTransient("일시"), fail_times=2)
    assert t.generate("ocr_verify", ocr_text="x") == "OK"
    assert t.calls == 3
    # 4xx류(RuntimeError)는 재시도하지 않고 즉시 전파
    r = _FlakyLLM(RuntimeError("400 bad request"), fail_times=1)
    try:
        r.generate("ocr_verify", ocr_text="x")
        assert False, "즉시 실패해야 함"
    except RuntimeError:
        pass
    assert r.calls == 1, f"재시도하면 안 됨(호출 {r.calls}회)"
    # LLMUnavailable(연결 불가)도 즉시 전파
    u = _FlakyLLM(LLMUnavailable("연결 불가"), fail_times=1)
    try:
        u.generate("ocr_verify", ocr_text="x")
        assert False
    except LLMUnavailable:
        pass
    assert u.calls == 1
    print("PASS test_retry_logic")


def test_prompt_render_error():
    # 프롬프트에 없는 변수는 무시되지만, 프롬프트가 요구하는 변수 누락은 명확한 에러
    m = MockLLM()
    try:
        m.generate("chatbot", context="c", question="q")   # history 누락
        assert False, "누락 플레이스홀더는 에러여야 함"
    except RuntimeError as e:
        assert "플레이스홀더" in str(e)
    print("PASS test_prompt_render_error")


def test_verifier():
    blocks = [
        Block(BlockType.PARAGRAPH, "저신뢰 텍스트", 1, {"confidence": 0.5}),
        Block(BlockType.PARAGRAPH, "고신뢰 텍스트", 1, {"confidence": 0.99}),
    ]
    out = verify_blocks(blocks, MockLLM())
    assert out[0].meta.get("verified") and out[0].meta["ocr_raw"] == "저신뢰 텍스트"
    assert not out[1].meta.get("verified"), "고신뢰 블록은 LLM 미투입이어야 함"
    print("PASS test_verifier")


if __name__ == "__main__":
    test_chunking()
    test_sql_guard()
    test_extract_sql_fence()
    test_tsquery_hyphen()
    test_subcommittee_routing()
    test_retry_logic()
    test_prompt_render_error()
    test_verifier()
