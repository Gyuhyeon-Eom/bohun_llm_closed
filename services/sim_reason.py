# -*- coding: utf-8 -*-
"""유사사례 'AI 왜 유사한지' 한 줄 요약 (260724).

각 상이처의 유사사례에 대해 본 건과의 공통점·주의할 차이를 LLM으로 요약해
sim_reason에 캐시한다. decision_doc이 similar 항목에 reason으로 주입.
사실 생성 금지 — 본 건·과거사례 요약문에 있는 정보만 사용.
"""
import psycopg
from psycopg.rows import dict_row

from config.settings import PG_DSN


def generate(app_id: int, llm, emb, per_dis: int = 4) -> dict:
    from services import decision_doc
    app = decision_doc.build_doc(app_id, emb)
    if not app:
        return {"error": "안건 없음"}
    made, skipped = 0, 0
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        for d in app["disabilities"]:
            case_txt = (f"{d['name']} ({d.get('body_side') or '-'} · {d.get('kcd_code')}) — "
                        f"{(d.get('onset_story') or '')[:150]}")
            for x in (d.get("similar") or [])[:per_dis]:
                cid = x.get("case_id")
                if cid is None:
                    continue
                cur.execute("SELECT 1 FROM sim_reason WHERE app_id=%s AND dis_id=%s AND case_id=%s",
                            (app_id, d["dis_id"], cid))
                if cur.fetchone():
                    skipped += 1
                    continue
                text = llm.generate("similar_reason", case=case_txt,
                                    decision=x.get("decision") or "미상",
                                    past=str(x.get("summary") or "")[:300])
                if text.startswith("[MOCK"):
                    return {"error": "생성 LLM 미연결(mock 모드) — FabriX/Ollama 연동 시 자동 요약됩니다",
                            "made": made}
                cur.execute("INSERT INTO sim_reason(app_id, dis_id, case_id, reason)"
                            " VALUES (%s,%s,%s,%s) ON CONFLICT (app_id, dis_id, case_id) DO NOTHING",
                            (app_id, d["dis_id"], cid, text.strip()[:200]))
                made += 1
        conn.commit()
    return {"ok": True, "made": made, "cached": skipped}
