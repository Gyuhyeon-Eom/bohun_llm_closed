# -*- coding: utf-8 -*-
"""Flow② 요건심사 심의의결서 초안 생성기: 안건 → 자료 수집 → 1~3장 + 4장 → 조립.

  자료 수집(build_doc): 인적·상이처·의무기록·병적·유사사례(경위 임베딩)·판례·AI 사전판단
        ↓
  1~3장 란별 초안(case_draft — 실물 의결서 문체·분과 정형화틀 v2·자료 우선순위 주입)
        ↓
  4장 종합판단(상이처별 draft_judgment — 이원 판단 + 리플렉시온 자체검증)
        ↓
  검증(required_done) → 조립(assemble — 결정적, LLM 미사용) → out/의결서_{안건}.txt|pdf

원칙(HITL): 판단 방향(국가유공자/보훈보상 해당·비해당)은 --verdict 미지정 시 AI 사전판단
추천값으로 '초안'을 만들 뿐, 확정·수정 권한은 담당자에게 있음을 매 실행 표출한다.

최적화: 란 병렬 생성(DRAFT_CONCURRENCY, 기본 1 — LLM 서빙 동시성에 맞춰 조정),
멱등(--force 아니면 기작성 란 스킵), 실패 란만 재시도 가능(런 매니페스트에 기록).
"""
import os
from concurrent.futures import ThreadPoolExecutor

from pipeline.runlog import RunLog

SECTIONS = ("s1", "s2", "s3")
DRAFT_CONCURRENCY = int(os.getenv("DRAFT_CONCURRENCY", "1"))


def run(app_id: int, verdict: str | None = None, force: bool = False,
        pdf: bool = False, out_dir: str = "out") -> dict:
    """verdict: 'yeu:해당,bosang:비해당' 형식 강제 지정(전 상이처 공통). 미지정=AI 추천값."""
    from core.llm_client import get_llm
    from ingestion.embedder import get_embedder
    from services import case_draft, decision_doc

    log = RunLog("decision", out_dir)
    llm, emb = get_llm(), get_embedder()

    # ① 자료 수집 — 무엇을 근거로 쓰는지 전부 표출
    with log.stage("자료 수집", app_id=app_id) as d:
        doc = decision_doc.build_doc(app_id, emb)   # app dict (disabilities 포함)
        if not doc:
            raise SystemExit(f"안건 {app_id} 없음")
        d.update(신청인=doc.get("applicant"), 접수번호=doc.get("recv_no"),
                 상이처=len(doc.get("disabilities", [])),
                 의무기록=len(doc.get("records", [])),
                 유사사례=sum(len(x.get("similar", [])) for x in doc.get("disabilities", [])))
        for dis in doc.get("disabilities", []):
            pre = dis.get("predicted") or {}
            if pre:
                log.info(f"AI 사전판단[{dis['name']}] (룰 기반 추천 — 확정은 담당자): "
                         f"유공 {pre.get('yeu_result')} / 보상 {pre.get('bosang_result')} "
                         f"(신뢰도 {pre.get('confidence')})")

    # ② 1~3장 란별 초안
    existing = case_draft.get_all(app_id)
    todo = [s for s in SECTIONS
            if force or not (existing.get(s) or {}).get("content")]
    with log.stage("1~3장 초안 생성", 대상=",".join(todo) or "(기작성 — 스킵)") as d:
        def gen(s):
            r = case_draft.generate(app_id, s, llm, emb)
            return s, len(r.get("content", ""))
        results = []
        if todo:
            with ThreadPoolExecutor(max_workers=max(1, DRAFT_CONCURRENCY)) as ex:
                results = list(ex.map(gen, todo))
        d.update(**{s: f"{n}자" for s, n in results})

    # ③ 4장 종합판단 — 상이처별
    with log.stage("4장 종합판단") as d:
        counts = {}
        for dis in doc.get("disabilities", []):
            dis_id = dis["dis_id"]
            if not force and _has_conclusion(app_id, dis_id):
                counts[dis["name"]] = "기작성 스킵"
                continue
            yeu, bosang = _resolve_verdict(verdict, dis)
            r = decision_doc.draft_judgment(app_id, dis_id, yeu, bosang, llm, emb)
            n_issues = len((r.get("reflexion") or {}).get("issues", []))
            counts[dis["name"]] = f"{len(r.get('body_text', ''))}자" + (
                f"(검증지적 {n_issues}건 반영)" if n_issues else "")
        d.update(**counts)
        log.info("판단내용은 추천값 기반 초안 — e통합보훈 내보내기 전 담당자 확정 필수")

    # ④ 검증 → ⑤ 조립 (결정적 — LLM 미사용)
    with log.stage("게이트 검증") as d:
        gate = case_draft.required_done(app_id)
        miss = (gate.get("missing_checks") or []) + (gate.get("empty_sections") or []) \
             + [f"판단 미생성: {n}" for n in gate.get("missing_judgment") or []]
        d.update(통과=gate.get("ok"), 미충족="; ".join(miss) or "없음")
    with log.stage("의결서 조립") as d:
        text = case_draft.assemble(app_id)
        safe = str(doc.get("recv_no") or app_id).replace("/", "-")
        out = os.path.join(out_dir, f"의결서_{safe}.txt")
        os.makedirs(out_dir, exist_ok=True)
        open(out, "w", encoding="utf-8").write(text)
        d.update(파일=out, 분량=f"{len(text)}자")
        if pdf:
            fname, pdf_path = decision_doc.export_pdf(app_id, emb)
            d.update(pdf=pdf_path)

    log.done(app_id=app_id)
    return {"run": str(log.path), "out": out, "gate": gate}


def _has_conclusion(app_id: int, dis_id: int) -> bool:
    import psycopg
    from config.settings import PG_DSN
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM conclusion WHERE app_id=%s AND dis_id=%s"
                    " AND COALESCE(body_text,'')<>''", (app_id, dis_id))
        return cur.fetchone() is not None


def _resolve_verdict(verdict: str | None, dis: dict) -> tuple[str, str]:
    """--verdict 'yeu:비해당,bosang:해당' > 상이처별 AI 사전판단 추천값 > 보수적 기본(비해당)."""
    if verdict:
        kv = dict(part.split(":", 1) for part in verdict.split(","))
        return kv.get("yeu", "비해당"), kv.get("bosang", "비해당")
    pre = dis.get("predicted") or {}
    return pre.get("yeu_result", "비해당"), pre.get("bosang_result", "비해당")
