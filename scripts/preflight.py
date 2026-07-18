"""폐쇄망 반입 직후 1회 실행하는 사전 점검. 서버를 띄우기 전에 환경을 검증한다.

실행: python3 scripts/preflight.py
점검: DB 접속(RW/RO) · 스키마 최신 여부 · 한글 폰트 · 생성 LLM 도달성 · 임베딩 모델 경로.
각 항목 PASS/FAIL을 출력하고, 하나라도 FAIL이면 종료코드 1(자동화에서 게이트로 사용 가능).

README의 "schema 먼저 안 하면 med_timeline 등 컬럼 없어 500" 경고를 자동으로 잡아준다.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import (PG_DSN, PG_DSN_RO, LLM_BACKEND, LLM_PROVIDER_LABEL,
                             FABRIX_ENDPOINT, FABRIX_MODEL, EMBED_MODEL, EMBED_BACKEND)

_results = []


def check(name: str, fn):
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, f"예외: {e}"
    _results.append(ok)
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name} — {detail}")


def _db(dsn: str, label: str):
    import psycopg
    with psycopg.connect(dsn, connect_timeout=5) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        cur.fetchone()
    return True, f"{label} 접속 OK"


def _schema_columns():
    """코드가 요구하는 신규 컬럼이 실제 존재하는지(스키마 적용 순서 누락 탐지)."""
    import psycopg
    required = [("application", "apply_kind"), ("application", "track"),
                ("grade_agenda", "med_timeline")]
    missing = []
    with psycopg.connect(PG_DSN, connect_timeout=5) as conn, conn.cursor() as cur:
        for table, col in required:
            cur.execute("""SELECT 1 FROM information_schema.columns
                           WHERE table_name=%s AND column_name=%s""", (table, col))
            if not cur.fetchone():
                missing.append(f"{table}.{col}")
    if missing:
        return False, ("누락 컬럼 " + ", ".join(missing) +
                       " — db/schema_case.sql을 먼저 적용하세요")
    return True, "필수 신규 컬럼 존재"


def _font():
    from services.decision_doc import register_korean_font
    name, src = register_korean_font()
    ok = name != "Helvetica"
    return ok, f"폰트={name} ({src})"


def _llm():
    if LLM_BACKEND != "openai":
        return False, f"LLM_BACKEND={LLM_BACKEND} (mock) — 운영은 openai + 엔드포인트 필요"
    import requests
    base = FABRIX_ENDPOINT.rsplit("/chat/completions", 1)[0]
    r = requests.get(f"{base}/models", timeout=5)
    r.raise_for_status()
    models = [m.get("id") for m in r.json().get("data", [])]
    ok = FABRIX_MODEL in models
    return ok, (f"{LLM_PROVIDER_LABEL} 연결됨, 모델 '{FABRIX_MODEL}' "
                + ("배포됨" if ok else f"미배포 (가용: {models[:5]})"))


def _embed_model():
    if EMBED_BACKEND != "bge":
        return False, f"EMBED_BACKEND={EMBED_BACKEND} (개발용 hash) — 운영은 bge 필요"
    if EMBED_MODEL.startswith(("/", ".", "~")):
        p = Path(os.path.expanduser(EMBED_MODEL))
        return p.is_dir(), f"로컬 모델 경로 {'존재' if p.is_dir() else '없음'}: {EMBED_MODEL}"
    return False, f"EMBED_MODEL={EMBED_MODEL} — 폐쇄망은 반입 모델의 로컬 절대경로여야 함"


def main():
    print("=== 보훈심사 AI 사전 점검 ===")
    check("DB 접속(RW, PG_DSN)", lambda: _db(PG_DSN, "PG_DSN"))
    check("DB 접속(RO, PG_DSN_RO)", lambda: _db(PG_DSN_RO, "PG_DSN_RO"))
    check("스키마 신규 컬럼", _schema_columns)
    check("PDF 한글 폰트", _font)
    check("생성 LLM 도달성", _llm)
    check("임베딩 모델", _embed_model)
    failed = _results.count(False)
    print(f"=== 결과: {_results.count(True)} PASS / {failed} FAIL ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
