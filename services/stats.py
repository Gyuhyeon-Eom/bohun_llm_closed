"""기능⑥ 대화형 통계 = Text-to-SQL(뷰 화이트리스트) -> 실행 -> LLM 자연어 요약."""
import re
import psycopg
from config.settings import PG_DSN
from core.llm_client import LLMClient

# 뷰 추가 시: 여기 + db/schema.sql 두 곳에 등록
ALLOWED_VIEWS = ["v_stats_by_review_type", "v_stats_by_year", "v_stats_by_kcd",
                 "v_stats_by_status", "v_stats_by_subcommittee", "v_stats_conclusion"]
MAX_ROWS = 200


def ask(question: str, llm: LLMClient) -> dict:
    sql = llm.generate("stats_sql", question=question,
                       allowed_views=", ".join(ALLOWED_VIEWS)).strip().rstrip(";")
    if sql == "UNSUPPORTED" or not _is_safe(sql):
        return {"error": "지원하지 않는 질의입니다", "sql": sql}
    try:
        with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
            cur.execute(sql)
            cols = [c.name for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchmany(MAX_ROWS)]
    except psycopg.Error as e:
        return {"error": f"질의 실행 실패: {e}", "sql": sql}
    answer = llm.generate("stats_answer", question=question, rows=str(rows[:30]))
    return {"sql": sql, "rows": rows, "answer": answer}


def _is_safe(sql: str) -> bool:
    s = sql.lower()
    if not s.lstrip().startswith("select") or ";" in s:
        return False
    tables = set(re.findall(r"(?:from|join)\s+([a-z_0-9]+)", s))
    return bool(tables) and tables.issubset(set(ALLOWED_VIEWS))
