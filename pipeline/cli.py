# -*- coding: utf-8 -*-
"""보훈심사 LLM 파이프라인 CLI — 폐쇄망 운영 진입점 (웹 불필요, API 설정만으로 동작).

  python -m pipeline ingest   <pdf|txt...> [--transcriber vlm|tesseract] [--convert case|grade]
  python -m pipeline decision <app_id> [--verdict yeu:해당,bosang:해당] [--force] [--pdf]
  python -m pipeline grade    [ga_id ...]
  python -m pipeline check    — 환경 사전점검 (LLM·VLM·DB·스토리지·임베딩)

환경변수(전부 여기서만 설정 — 코드 수정 불필요):
  LLM_BACKEND=openai FABRIX_ENDPOINT=... FABRIX_MODEL=...   생성 LLM (FabriX/vLLM/Ollama)
  VLM_ENDPOINT=... VLM_MODEL=...                            비전 전사 (ingest)
  EMBED_BACKEND=bge EMBED_MODEL=/models/bge-m3              임베딩 (반입 모델 경로)
  PG_DSN=postgresql://...                                   DB
  STORAGE_BACKEND=minio MINIO_ENDPOINT=... MINIO_ACCESS_KEY=... MINIO_SECRET_KEY=...
"""
import argparse
import sys


def _print_config():
    from config import settings as st
    import os
    print("── 구성 ─────────────────────────────────────")
    print(f"  LLM     : {st.LLM_BACKEND}"
          + (f" → {st.FABRIX_ENDPOINT} ({st.FABRIX_MODEL})" if st.LLM_BACKEND == "openai" else " (mock — 운영 전환 필요)"))
    print(f"  VLM     : {os.getenv('VLM_ENDPOINT', '(미설정 — ingest는 --transcriber tesseract 사용 가능)')}"
          + (f" ({os.getenv('VLM_MODEL')})" if os.getenv('VLM_MODEL') else ""))
    print(f"  임베딩  : {st.EMBED_BACKEND} ({st.EMBED_MODEL})")
    print(f"  DB      : {st.PG_DSN.split('@')[-1]}")
    print(f"  스토리지: {st.STORAGE_BACKEND}"
          + (f" → {st.MINIO_ENDPOINT}/{st.MINIO_BUCKET}" if st.STORAGE_BACKEND == "minio" else ""))
    print(f"  오케스트레이션: {st.ORCH_BACKEND}")
    print("─────────────────────────────────────────────")


def check() -> int:
    """운영 투입 전 사전점검 — 실패 항목과 조치 방법을 그대로 표출."""
    import os
    import requests as rq
    from config import settings as st
    ok = True

    def probe(name, fn, fix):
        nonlocal ok
        try:
            detail = fn() or ""
            print(f"  ✔ {name} {detail}")
        except Exception as e:
            ok = False
            print(f"  ✘ {name}: {e}\n     조치: {fix}")

    def _db():
        import psycopg
        with psycopg.connect(st.PG_DSN, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM documents")
            n = cur.fetchone()[0]
            cur.execute("SELECT extname FROM pg_extension WHERE extname='vector'")
            assert cur.fetchone(), "pgvector 확장 미설치"
            return f"(코퍼스 문서 {n}건)"

    def _llm():
        assert st.LLM_BACKEND == "openai", "LLM_BACKEND=mock — FABRIX_* 설정 필요"
        r = rq.get(st.FABRIX_ENDPOINT.rsplit("/chat/", 1)[0].rstrip("/") + "/models",
                   headers={"Authorization": f"Bearer {st.FABRIX_API_KEY}"}, timeout=5)
        r.raise_for_status()
        return f"({st.FABRIX_MODEL})"

    def _vlm():
        ep = os.getenv("VLM_ENDPOINT")
        assert ep, "VLM_ENDPOINT 미설정 (VLM 전사 안 쓰면 무시 가능)"
        rq.get(ep.rsplit("/chat/", 1)[0].rstrip("/") + "/models", timeout=5)
        return f"({os.getenv('VLM_MODEL')})"

    def _emb():
        from ingestion.embedder import get_embedder
        v = get_embedder().encode(["점검"])[0]
        assert len(v) == st.EMBED_DIM, f"차원 불일치 {len(v)}≠{st.EMBED_DIM}"
        return f"({st.EMBED_BACKEND}, {st.EMBED_DIM}차원)"

    def _storage():
        from core.storage import get_storage
        s = get_storage()
        if st.STORAGE_BACKEND == "minio":
            assert s.backend == "minio", "MinIO 접속 실패 — local 폴백 상태"
        return f"({s.backend})"

    print("사전점검:")
    probe("DB(PostgreSQL+pgvector)", _db, "docker compose up -d db / PG_DSN 확인")
    probe("생성 LLM", _llm, "LLM_BACKEND=openai FABRIX_ENDPOINT/MODEL/API_KEY 설정")
    probe("VLM(비전 전사)", _vlm, "VLM_ENDPOINT/VLM_MODEL 설정 (FabriX 이미지 입력 규격)")
    probe("임베딩", _emb, "EMBED_BACKEND=bge EMBED_MODEL=<bge-m3 반입 경로>")
    probe("객체 스토리지", _storage, "docker compose up -d minio / MINIO_* 확인")
    print("결과:", "정상 — 파이프라인 실행 가능" if ok else "실패 항목 조치 후 재점검")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pipeline", description="보훈심사 LLM 파이프라인")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("ingest", help="Flow① 스캔→VLM 전사→적재→정규화→RAG 색인")
    p1.add_argument("files", nargs="+", help="스캔 PDF 또는 기 OCR txt")
    p1.add_argument("--transcriber", choices=["vlm", "tesseract"], default="vlm")
    p1.add_argument("--dpi", type=int, default=260)
    p1.add_argument("--no-normalize", action="store_true", help="LLM 정규화 생략")
    p1.add_argument("--no-index", action="store_true", help="RAG 색인 생략")
    p1.add_argument("--convert", choices=["case", "grade"], help="적재 후 안건 변환")

    p2 = sub.add_parser("decision", help="Flow② 요건심사 심의의결서 초안 생성")
    p2.add_argument("app_id", type=int)
    p2.add_argument("--verdict", help="판단 강제 지정 예: yeu:해당,bosang:비해당 (미지정=AI 추천값)")
    p2.add_argument("--force", action="store_true", help="기작성 란·판단도 재생성")
    p2.add_argument("--pdf", action="store_true", help="PDF 동시 산출")

    p3 = sub.add_parser("grade", help="Flow③ 상이등급 심사표 XLSX 생성")
    p3.add_argument("ga_ids", nargs="*", type=int, help="미지정=전체 안건")

    sub.add_parser("check", help="환경 사전점검")

    for p in (p1, p2, p3):
        p.add_argument("--out", default="out", help="산출물 디렉터리 (기본 out/)")

    a = ap.parse_args(argv)
    _print_config()
    if a.cmd == "check":
        return check()
    if a.cmd == "ingest":
        from pipeline.flow_ingest import run
        run(a.files, transcriber=a.transcriber, dpi=a.dpi,
            normalize=not a.no_normalize, index=not a.no_index,
            convert=a.convert, out_dir=a.out)
    elif a.cmd == "decision":
        from pipeline.flow_decision import run
        run(a.app_id, verdict=a.verdict, force=a.force, pdf=a.pdf, out_dir=a.out)
    elif a.cmd == "grade":
        from pipeline.flow_grade import run
        run(a.ga_ids or None, out_dir=a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
