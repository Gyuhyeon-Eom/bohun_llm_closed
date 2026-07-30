# -*- coding: utf-8 -*-
"""Flow③ 상이등급 심사표 XLSX 생성기: 안건 → 별표3 대조·유사 등급사례 → 심사표 엑셀.

  안건 로드(injury_items·신검등급·이전 심사) → 상이처별 AI 등급예측
  (별표3 기준 189건 임베딩+어휘 대조 → 1·2순위 등급 + 원문 인용 사유,
   과거 등급사례 유사 조회 — 둘 다 결정적 조립, 생성 LLM 미사용)
        ↓
  실물 심사표 서식 XLSX 산출 (담당자 확정등급 > 신검등급 > AI 순 우선)

표출: 안건·상이처별 예측 근거(별표3 분류번호·유사사례 수)와 산출 파일 경로.
멱등: 산출은 항상 재계산(예측은 캐시 안 함 — 별표3·사례 풀 갱신을 즉시 반영).
"""
import os
import shutil

from pipeline.runlog import RunLog


def run(ga_ids: list[int] | None = None, out_dir: str = "out") -> dict:
    """ga_ids 미지정 = 전체 안건 일괄."""
    import psycopg
    from psycopg.rows import dict_row
    from config.settings import PG_DSN
    from ingestion.embedder import get_embedder
    from services import grade_export

    log = RunLog("grade", out_dir)
    emb = get_embedder()
    os.makedirs(out_dir, exist_ok=True)

    with log.stage("안건 조회") as d:
        with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
            if ga_ids:
                cur.execute("SELECT ga_id, agenda_no, applicant AS person FROM grade_agenda"
                            " WHERE ga_id = ANY(%s) ORDER BY ga_id", (ga_ids,))
            else:
                cur.execute("SELECT ga_id, agenda_no, applicant AS person FROM grade_agenda ORDER BY ga_id")
            agendas = cur.fetchall()
        d.update(안건수=len(agendas))
        if not agendas:
            raise SystemExit("대상 안건 없음")

    outs = []
    for ag in agendas:
        with log.stage("심사표 생성", ga_id=ag["ga_id"], 안건=ag.get("agenda_no")) as d:
            fname, tmp = grade_export.export_xlsx(ag["ga_id"], emb)
            dest = os.path.join(out_dir, fname)
            shutil.move(tmp, dest)
            d.update(파일=dest, 대상자=ag.get("person"))
            outs.append(dest)

    log.done(files=len(outs))
    return {"run": str(log.path), "files": outs}
