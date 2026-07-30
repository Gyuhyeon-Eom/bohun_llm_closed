# -*- coding: utf-8 -*-
"""Flow① 문서접수: 스캔 PDF/OCR txt → 전사(VLM) → 적재 → LLM 정규화 → RAG 색인.

  PDF ─┬─ 텍스트층 있음 ────────────────┐
       └─ 없음 → VLM 전사(타일·병렬·재시도) ┤→ 파싱 → scan_doc/file_page + MinIO 원본
  txt(사이냅 산출) ─────────────────────┘         ↓
                                        LLM 필드 정규화(블록별, 교정쌍 few-shot)
                                                  ↓
                                        정리본 조립 → RAG 색인(청킹·임베딩)
                                                  ↓ (--to-case/--to-grade)
                                        요건심사 사건 / 상이등급 안건 변환

최적화:
  - 페이지 병렬 전사(VLM_CONCURRENCY, 기본 2 — 서빙 GPU 동시성에 맞춰 조정)
  - 재시도 3회 지수백오프(1·2·4초), 타일링(비전 인코더 해상도 한계 대응)
  - 멱등: 같은 파일명 재실행 시 대체, 정규화는 미처리 블록만(force로 전체)
  - 환각 방어: 전사 전용 프롬프트 + 기계검사(반복 루프·과소 전사) → file_page 검수 플래그
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pipeline.runlog import RunLog

VLM_CONCURRENCY = int(os.getenv("VLM_CONCURRENCY", "2"))
VLM_TILES = int(os.getenv("VLM_TILES", "3"))
VLM_RETRIES = 3


def _transcribe_page(png: bytes, transcriber: str) -> tuple[str, list[str]]:
    """페이지 1장 전사 (+환각 기계검사 경고). 실패는 지수백오프 재시도 후 전파."""
    from scripts.vlm_ocr import (clean_transcript, merge_tiles, sanity, split_tiles,
                                 vlm_transcribe)
    last = None
    for attempt in range(VLM_RETRIES):
        try:
            if transcriber == "tesseract":
                import io
                import pytesseract
                from PIL import Image
                return pytesseract.image_to_string(Image.open(io.BytesIO(png)),
                                                   lang="kor+eng", timeout=120), []
            text = clean_transcript(merge_tiles(
                [vlm_transcribe(t) for t in split_tiles(png, VLM_TILES)]))
            return text, sanity(text)
        except Exception as e:
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"전사 {VLM_RETRIES}회 실패: {last}")


def run(paths: list[str], transcriber: str = "vlm", dpi: int = 260,
        normalize: bool = True, index: bool = True, convert: str | None = None,
        out_dir: str = "out") -> dict:
    """convert: None | 'case'(요건심사 사건) | 'grade'(상이등급 안건)"""
    from core.llm_client import get_llm
    from ingestion.embedder import get_embedder
    from scripts.ocr_ingest_scans import ingest_real_txt, insert_scan_doc
    from scripts.vlm_ocr import render_pages
    from services import ocr_normalize, scan_to_case

    log = RunLog("ingest", out_dir)
    llm = get_llm() if normalize else None
    emb = get_embedder() if index else None
    results = []

    for path in paths:
        p = Path(path)
        log.info(f"── {p.name}")

        # ① 전사 (txt는 기 OCR 산출물 — 전사 생략)
        if transcriber == "parsing" and p.suffix.lower() != ".txt":
            # Parsing API(260730): 문서→페이지 텍스트 — VLM 없이 플랫폼 파서 사용
            with log.stage("전사(Parsing API)", file=p.name) as d:
                from core.provider_apis import parse_document
                texts = parse_document(str(p))
                warns_all = []
                d.update(pages=len(texts), chars=sum(len(t) for t in texts))
            with log.stage("파싱·적재") as d:
                sd_id, head, nblk = insert_scan_doc(texts, str(p), ocr_used=True)
                d.update(sd_id=sd_id, person=_mask(head.get("person")), blocks=nblk,
                         storage=_storage_backend())
        elif p.suffix.lower() == ".txt":
            with log.stage("적재(기 OCR txt)", file=p.name) as d:
                sd_id, person, disease, nblk = ingest_real_txt(str(p))
                d.update(sd_id=sd_id, person=_mask(person), blocks=nblk)
        else:
            with log.stage("전사", file=p.name, transcriber=transcriber) as d:
                pages = list(render_pages(p, dpi))
                warns_all = []
                # 병렬은 네트워크 I/O인 VLM만 — tesseract는 서브프로세스라 순차가 안전
                workers = VLM_CONCURRENCY if transcriber == "vlm" else 1
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    outs = list(ex.map(lambda pg: _transcribe_page(pg[1], transcriber), pages))
                texts = [t for t, _ in outs]
                for no, (_, warns) in enumerate(outs, 1):
                    for w in warns:
                        log.warn(f"p{no}: {w} — 검수 필수", page=no)
                        warns_all.append(no)
                d.update(pages=len(texts), chars=sum(len(t) for t in texts),
                         warn_pages=sorted(set(warns_all)))
            with log.stage("파싱·적재") as d:
                sd_id, head, nblk = insert_scan_doc(texts, str(p),
                                                    ocr_used=(transcriber != "none"))
                d.update(sd_id=sd_id, person=_mask(head.get("person")), blocks=nblk,
                         storage=_storage_backend())
                _flag_review_pages(sd_id, warns_all)

        # ② LLM 필드 정규화 (블록별 — 실패 블록은 규칙 폴백, 전체 파이프라인은 계속)
        if normalize:
            with log.stage("LLM 정규화", sd_id=sd_id) as d:
                r = ocr_normalize.normalize_scan(sd_id, llm=llm)
                d.update(**{k: r[k] for k in ("total", "llm", "rule") if k in r})

        # ③ RAG 색인 (정리본 → 청킹·임베딩 → 챗봇·초안 근거 검색 대상)
        if index:
            with log.stage("RAG 색인", sd_id=sd_id) as d:
                r = ocr_normalize.index_clean(sd_id, emb)
                d.update(**{k: v for k, v in r.items() if isinstance(v, (int, str))})

        # ④ 안건 변환 (선택 — HITL: 변환 결과의 확정·보정은 담당자)
        if convert:
            with log.stage(f"안건 변환({convert})", sd_id=sd_id) as d:
                r = (scan_to_case.to_case(sd_id) if convert == "case"
                     else scan_to_case.to_grade(sd_id))
                d.update(**{k: v for k, v in r.items() if isinstance(v, (int, str))})

        results.append({"file": p.name, "sd_id": sd_id})

    log.done(files=len(results))
    return {"run": str(log.path), "results": results}


def _mask(name) -> str | None:
    """런 매니페스트에 실명 미기록 (개인정보 정책 — 로그·산출 로그도 마스킹)."""
    return None if not name else (name[0] + "O" + name[2:] if len(name) >= 3 else name[0] + "O")


def _storage_backend() -> str:
    from core.storage import get_storage
    return get_storage().backend


def _flag_review_pages(sd_id: int, pages: list[int]):
    """환각 기계검사에 걸린 페이지 → file_page 검수 필요(reviewed=false 유지 + confidence 0)."""
    if not pages:
        return
    import psycopg
    from config.settings import PG_DSN
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        for no in set(pages):
            cur.execute("UPDATE file_page SET confidence=0 WHERE sd_id=%s AND page_no=%s",
                        (sd_id, no))
        conn.commit()
