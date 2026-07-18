"""기능⑥ 대화형 통계 = Text-to-SQL(뷰 화이트리스트) -> 실행 -> LLM 자연어 요약.

방어(폐쇄망·개인의료정보):
- SQL을 정규식이 아니라 sqlglot AST로 검증한다. 콤마 조인·서브쿼리·CTE로 화이트리스트
  밖 테이블(application/medical_record 등)을 끌어오는 우회를 구조적으로 차단.
- 실행은 읽기전용 DSN(PG_DSN_RO) + statement_timeout으로 2차 방어(권한·시간 상한).
- LLM 응답의 ```sql 코드펜스를 벗겨 실제 SQL만 검증·실행한다.
"""
import re
import psycopg
import sqlglot
from sqlglot import exp
from config.settings import PG_DSN_RO
from core.llm_client import LLMClient

# 뷰 추가 시: 여기 + db/schema.sql 두 곳에 등록
ALLOWED_VIEWS = ["v_stats_by_review_type", "v_stats_by_year", "v_stats_by_kcd",
                 "v_stats_by_status", "v_stats_by_subcommittee", "v_stats_conclusion"]
MAX_ROWS = 200
STATEMENT_TIMEOUT_MS = 5000
# 명백히 위험한 함수(정보 노출·DoS·부작용) 이름 차단 — 구조 검증에 더한 방어선.
_DANGEROUS_FUNCS = {"pg_sleep", "pg_read_file", "pg_read_binary_file", "pg_ls_dir",
                    "dblink", "lo_import", "lo_export", "query_to_xml", "copy",
                    "current_setting", "set_config", "pg_terminate_backend"}


def _extract_sql(raw: str) -> str:
    """LLM 응답에서 SQL만 추출. ```sql ... ``` 펜스가 있으면 그 안을, 없으면 전체를 사용."""
    m = re.search(r"```(?:sql)?\s*(.+?)```", raw, re.S | re.I)
    sql = m.group(1) if m else raw
    return sql.strip().rstrip(";").strip()


def _is_safe(sql: str) -> bool:
    """단일 SELECT이고, 참조하는 모든 테이블이 ALLOWED_VIEWS 부분집합이며,
    위험 함수를 쓰지 않는지 sqlglot AST로 검증한다."""
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except Exception:
        return False
    statements = [s for s in statements if s is not None]
    if len(statements) != 1:                       # 다중 statement 차단
        return False
    root = statements[0]
    if not isinstance(root, exp.Select):           # 최상위가 SELECT가 아니면 차단(INSERT/UPDATE/DELETE/DDL)
        return False
    # 참조 테이블 전부가 화이트리스트 안에 있어야 함(콤마 조인·서브쿼리·CTE 모두 AST로 포착)
    tables = {t.name for t in root.find_all(exp.Table)}
    if not tables or not tables.issubset(set(ALLOWED_VIEWS)):
        return False
    # 위험 함수 차단. 알려진 함수는 sql_name(), 임의 함수(pg_sleep 등)는 .name에 이름이 담긴다.
    for fn in root.find_all(exp.Func, exp.Anonymous):
        name = (getattr(fn, "name", "") or fn.sql_name() or "").lower()
        if name in _DANGEROUS_FUNCS:
            return False
    return True


def ask(question: str, llm: LLMClient) -> dict:
    raw = llm.generate("stats_sql", question=question, allowed_views=", ".join(ALLOWED_VIEWS))
    sql = _extract_sql(raw)
    if sql.upper() == "UNSUPPORTED" or not _is_safe(sql):
        return {"error": "지원하지 않는 질의입니다"}   # SQL 원문은 노출하지 않음(내부 스키마 보호)
    try:
        with psycopg.connect(PG_DSN_RO) as conn, conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")
            cur.execute(sql)
            cols = [c.name for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchmany(MAX_ROWS)]
    except psycopg.Error as e:
        # 사용자에겐 요약만. 원문 SQL·상세 에러는 서버 로그로.
        print(f"[stats] 실행 실패 sql={sql!r} err={e}")
        return {"error": "질의 실행에 실패했습니다"}
    answer = llm.generate("stats_answer", question=question, rows=str(rows[:30]))
    return {"sql": sql, "rows": rows, "answer": answer}
