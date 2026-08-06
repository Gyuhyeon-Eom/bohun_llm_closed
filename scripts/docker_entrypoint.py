# -*- coding: utf-8 -*-
"""도커 API 컨테이너 엔트리포인트 — DB 대기 → 스키마 멱등 적용 → uvicorn.

동작:
  1. PG_DSN 접속 대기 (DB 컨테이너 healthcheck와 별개로 이중 방어, 최대 60초)
  2. db/schema.sql → schema_case.sql → graph_schema.sql 순 멱등 적용
     (CREATE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS 기반 — 기존 데이터 무손실)
  3. SEED_ON_START=1 이면 scripts/apply_db_updates.py 실행 (판단기준 룰·그래프 재적재)
     + 빈 DB면 목데이터 시드 (DEMO_SEED=1일 때만)
  4. uvicorn 기동

폐쇄망 규칙: .sh 금지 — 엔트리포인트도 .py.
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import PG_DSN  # noqa: E402


def wait_db(timeout_s: int = 60):
    import psycopg
    t0 = time.time()
    while True:
        try:
            with psycopg.connect(PG_DSN, connect_timeout=3):
                print("[entrypoint] DB 연결 확인")
                return
        except Exception as e:
            if time.time() - t0 > timeout_s:
                print(f"[entrypoint] DB 연결 실패({e}) — 기동 중단", file=sys.stderr)
                raise
            time.sleep(2)


def apply_schema():
    import psycopg
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        for name in ("schema.sql", "schema_case.sql", "graph_schema.sql"):
            path = os.path.join(root, "db", name)
            if os.path.exists(path):
                cur.execute(open(path, encoding="utf-8").read())
                print(f"[entrypoint] {name} 적용")
        conn.commit()


def main():
    wait_db()
    apply_schema()
    if os.getenv("SEED_ON_START") == "1":
        subprocess.run([sys.executable, "scripts/apply_db_updates.py"], check=False)
    if os.getenv("DEMO_SEED") == "1":
        import psycopg
        with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM application")
            empty = cur.fetchone()[0] == 0
        if empty:
            subprocess.run([sys.executable, "-m", "mockgen.generate_cases"], check=False)
    port = os.getenv("PORT", "8000")
    os.execvp(sys.executable, [sys.executable, "-m", "uvicorn", "api.main:app",
                               "--host", "0.0.0.0", "--port", port])


if __name__ == "__main__":
    main()
