# -*- coding: utf-8 -*-
"""사건 자료함 — 자료·파일을 행 단위(case_file)로 관리, '최종 자료' 지정.

배경(담당자 피드백): 출처를 하드코딩 툴팁으로 붙이는 대신, 자료 자체를 DB 행으로
쌓고(추가 자료가 와도 INSERT 한 줄 — JSON append 불필요) 담당자가 그중
'최종 자료'(is_final)를 지정한다. 화면의 근거 표시는 이 목록을 참조한다.

- 자동 파생: 기존 사건 데이터(기본서류·의무기록·공적서류·스캔 원문)에서 행 생성
  (멱등 — UNIQUE(app_id, dis_id, kind, title) ON CONFLICT DO NOTHING)
- 추가: 담당자 메타 등록 또는 파일 업로드 (data/case_files/ 저장)
"""
import os

import psycopg
from psycopg.rows import dict_row

from config.settings import PG_DSN

UPLOAD_DIR = os.path.join("data", "case_files")


def _ins(cur, app_id, kind, title, dis_id=None, file_name=None, file_path=None,
         note=None, is_final=False, by="시스템"):
    cur.execute("""INSERT INTO case_file(app_id, dis_id, kind, title, file_name, file_path,
                   note, is_final, uploaded_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (app_id, COALESCE(dis_id,0), kind, title) DO NOTHING""",
                (app_id, dis_id, kind, title[:200], file_name, file_path, note, is_final, by))


def sync(app_id: int) -> int:
    """기존 사건 데이터에서 자료 행 자동 파생 (멱등). 반환: 현재 자료 수."""
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM application WHERE app_id=%s", (app_id,))
        app = cur.fetchone()
        if not app:
            return 0
        # 기본 서류 (정형화틀 관련서류 열 기준 — 최초 근거 세트라 최종 자료로 시작)
        _ins(cur, app_id, "신청서", "1-1 신청서·요건발급요청서", is_final=True)
        _ins(cur, app_id, "병적", "1-4 병적증명서·인사자력표", is_final=True)
        cur.execute("SELECT * FROM disability WHERE app_id=%s ORDER BY dis_id", (app_id,))
        diss = cur.fetchall()
        for d in diss:
            _ins(cur, app_id, "요건사실확인서", f"1-4 요건사실확인서 — {d['name']}",
                 dis_id=d["dis_id"], is_final=True)
            cur.execute("SELECT * FROM medical_record WHERE dis_id=%s ORDER BY rec_date",
                        (d["dis_id"],))
            for m in cur.fetchall():
                _ins(cur, app_id, "의무기록",
                     f"{m['hospital']} {m['rec_type']} ({m['rec_date'] or '일자미상'})",
                     dis_id=d["dis_id"], note=(m.get("diagnosis") or m.get("finding") or "")[:150],
                     is_final=m["rec_type"] in ("영상", "수술"))
        cur.execute("SELECT * FROM official_doc WHERE app_id=%s ORDER BY od_id", (app_id,))
        for o in cur.fetchall():
            _ins(cur, app_id, "공적서류", f"{o['doc_kind']} ({o['doc_date'] or ''} {o['issuer'] or ''})".strip(),
                 dis_id=o["dis_id"], note=(o.get("content") or "")[:150])
        cur.execute("SELECT sd_id, file_name, orig_path, doc_kind FROM scan_doc WHERE app_id=%s",
                    (app_id,))
        for sd in cur.fetchall():
            _ins(cur, app_id, "스캔 원문", f"{sd['doc_kind']} — {sd['file_name']}",
                 file_name=sd["file_name"], file_path=sd["orig_path"])
        conn.commit()
        cur.execute("SELECT count(*) FROM case_file WHERE app_id=%s", (app_id,))
        return cur.fetchone()["count"]


def list_files(app_id: int) -> list[dict]:
    """자료 목록 (없으면 자동 파생 후). 최종 자료 먼저, 종류·등록순."""
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM case_file WHERE app_id=%s", (app_id,))
        if cur.fetchone()["count"] == 0:
            pass
    sync(app_id)   # 멱등 — 새 의무기록·스캔이 연결되면 목록에도 나타난다
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("""SELECT * FROM case_file WHERE app_id=%s
                       ORDER BY is_final DESC, kind, cf_id""", (app_id,))
        return cur.fetchall()


def add(app_id: int, kind: str, title: str, dis_id=None, note=None,
        file_name=None, file_path=None, by="담당자") -> dict:
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        _ins(cur, app_id, kind or "추가 자료", title, dis_id=dis_id, note=note,
             file_name=file_name, file_path=file_path, by=by)
        conn.commit()
    return {"ok": True}


def save_upload(app_id: int, filename: str, data: bytes, kind: str, note=None) -> dict:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    safe = filename.replace("/", "_").replace("\\", "_")
    path = os.path.join(UPLOAD_DIR, f"app{app_id}_{safe}")
    with open(path, "wb") as f:
        f.write(data)
    add(app_id, kind or "추가 자료", safe, note=note, file_name=safe, file_path=path)
    return {"ok": True, "file_name": safe}


def set_final(cf_id: int, is_final: bool) -> dict:
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("UPDATE case_file SET is_final=%s WHERE cf_id=%s", (is_final, cf_id))
        conn.commit()
    return {"ok": True, "cf_id": cf_id, "is_final": is_final}


def get_file(cf_id: int) -> dict | None:
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM case_file WHERE cf_id=%s", (cf_id,))
        return cur.fetchone()
