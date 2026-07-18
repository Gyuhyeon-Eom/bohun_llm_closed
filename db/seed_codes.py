"""db/seed/*.csv -> PostgreSQL 적재. 재실행 시 코드 테이블은 truncate 후 재적재 (멱등)."""
import csv, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))  # 직접 실행 시 프로젝트 루트 인식
import psycopg
from config.settings import PG_DSN

SEED = Path(__file__).parent / "seed"
TABLES = ["review_type", "exam_category", "exam_reason", "standard_clause",
          "review_content", "agenda", "auto_order_rule", "kcd"]


def load(conn, table: str):
    path = SEED / f"{table}.csv"
    with open(path, encoding="utf-8") as f:
        header = f.readline().strip().split(",")
    cols = ",".join(header)
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE {table} RESTART IDENTITY CASCADE")
        with open(path, encoding="utf-8") as f, cur.copy(
                f"COPY {table}({cols}) FROM STDIN WITH (FORMAT csv, HEADER true)") as cp:
            while data := f.read(1 << 20):
                cp.write(data)
        cur.execute(f"SELECT count(*) FROM {table}")
        print(f"  {table}: {cur.fetchone()[0]}행")


def main():
    with psycopg.connect(PG_DSN) as conn:
        for t in TABLES:
            load(conn, t)


if __name__ == "__main__":
    main()
