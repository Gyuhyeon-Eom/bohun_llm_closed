# -*- coding: utf-8 -*-
"""Flow④ 통합보훈 연계 워커: 폴링 → 39 큐(link_prcs) 적재 → 단계 실행 → 전송.

  통합보훈 DB(읽기 전용) ── 폴링(신규 안건키) ──→ link_prcs INSERT ON CONFLICT DO NOTHING
        ↓ 픽업(FOR UPDATE SKIP LOCKED, 배치)
  수집 → VLM추출 → 임베딩 → 초안생성 → 전송 → 완료
        (실패하면 그 단계에 멈춤 — 재시도는 실패 단계부터 재개, retry_cnt < LINK_RETRY_MAX)

설계 (명세 v0.9 · 39_연계처리):
  - 큐는 안건당 1행 갱신(단일 진실원장), 작업 이력 누적은 12b_AI처리작업 몫 — 역할 분리.
  - 좀비 회수: '처리중' AND updated_at < LINK_ZOMBIE_MIN분 전 → '대기' 복귀.
  - 스케줄러: APScheduler(반입 시) / 미반입 환경은 sleep 루프 폴백 — 동작 동일.
  - 전송은 통합보훈 쓰기 권한 협의 미결 — 기본 LINK_SEND_MODE=hold(우리 PG 보관,
    프론트는 API 조회). 협의 확정 시 db 모드의 _send_to_dest만 구현하면 된다.

개발 모드(LINK_SRC_DSN 미설정): data/link_inbox/<원천안건키>/ 에 PDF·txt를 떨어뜨리면
디렉토리명이 원천안건키로 큐에 올라 전 단계가 실행된다 — 실연계 전 파이프라인 검증용.

실행:
  python -m pipeline link --once          # 1회 순회 (폴링→처리→종료)
  python -m pipeline link --interval 60   # 상주 워커 (기본 LINK_POLL_S)

단계별 워커 분리(--steps): 같은 큐(link_prcs)를 담당 단계로 나눠 맡아 파이프라이닝 —
자원 특성(VLM은 GPU/외부 API, 임베딩은 CPU, 초안은 LLM)별 배치·주기 튜닝과 장애 격리.
  python -m pipeline link --steps 수집,VLM추출          # 파일 처리 워커
  python -m pipeline link --steps 임베딩,초안생성,전송   # 적재·생성 워커
큐는 하나(안건당 1행, 명세 v0.9) — 워커는 담당 단계만 픽업하고, 담당 밖 단계에
도달하면 '대기'로 인계한다. 원천 폴링은 수집 담당 워커만 수행.
"""
import json
import re
import time
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from config.settings import (
    BATCH_STEPS, LINK_BATCH, LINK_DEST_DSN, LINK_INBOX, LINK_POLL_S,
    LINK_RETRY_MAX, LINK_SEND_MODE, LINK_SRC_DSN, LINK_SRC_QUERY,
    LINK_ZOMBIE_MIN, PG_DSN, UPLOAD_INBOX, UPLOAD_PREFIX, UPLOAD_SWEEP_MAX,
)
from pipeline.batch_window import in_batch_window
from pipeline.runlog import RunLog

# 처리단계코드 — 순서가 곧 체인. '완료'는 말단 표지라 체인에 없다.
STEP_CHAIN = ("수집", "VLM추출", "임베딩", "초안생성", "전송")


def _ymd(v) -> str | None:
    """원천 일자(VARCHAR2 YYYYMMDD) → ISO 날짜. 비정상 값은 None(적재 차단 방지)."""
    s = re.sub(r"\D", "", str(v or ""))
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else None


# ── 큐 적재 (폴링) ───────────────────────────────────────────────────────────

def enqueue_new(cur) -> int:
    """신규 원천안건키를 큐에 적재 — 이미 있는 키는 ON CONFLICT DO NOTHING(안건당 1행)."""
    keys = _poll_source_keys()
    n = 0
    for k in keys:
        cur.execute("INSERT INTO link_prcs (src_case_key) VALUES (%s)"
                    " ON CONFLICT (src_case_key) DO NOTHING", (k,))
        n += cur.rowcount
    return n


def _poll_source_keys() -> list[str]:
    if LINK_SRC_DSN:
        from ingestion.link_collector import Q_POLL_DEFAULT
        with psycopg.connect(LINK_SRC_DSN) as src, src.cursor() as cur:
            cur.execute(LINK_SRC_QUERY or Q_POLL_DEFAULT)   # 기본: RV_AGND 폴링
            return [str(r[0]) for r in cur.fetchall()]
    # 개발 모드: 수집함 디렉토리명 = 원천안건키
    inbox = Path(LINK_INBOX)
    if not inbox.is_dir():
        return []
    return sorted(d.name for d in inbox.iterdir() if d.is_dir() and any(d.iterdir()))


# ── 큐 상태 전이 ─────────────────────────────────────────────────────────────

def reap_zombies(cur) -> int:
    """워커 사망으로 방치된 '처리중' 회수 — 수정일시 기준(명세 v0.9)."""
    cur.execute("UPDATE link_prcs SET stat_cd='대기', updated_at=now()"
                " WHERE stat_cd='처리중' AND updated_at < now() - %s * interval '1 minute'",
                (LINK_ZOMBIE_MIN,))
    return cur.rowcount


def pickup(cur, limit: int = LINK_BATCH, steps: tuple[str, ...] | None = None) -> list[dict]:
    """처리 대상 픽업 — 다중 워커 안전(FOR UPDATE SKIP LOCKED), 픽업 즉시 '처리중'.
    steps 지정 시 담당 단계의 안건만 픽업 — 단계별 워커 분리(파일 처리/적재·임베딩 등)."""
    cur.execute("""
        WITH picked AS (
          SELECT link_id FROM link_prcs
          WHERE (stat_cd = '대기' OR (stat_cd = '실패' AND retry_cnt < %(max)s))
            AND (%(steps)s::text[] IS NULL OR step_cd = ANY(%(steps)s))
          ORDER BY link_id
          FOR UPDATE SKIP LOCKED
          LIMIT %(n)s)
        UPDATE link_prcs l SET stat_cd='처리중', updated_at=now()
        FROM picked WHERE l.link_id = picked.link_id
        RETURNING l.*""",
        {"max": LINK_RETRY_MAX, "n": limit, "steps": list(steps) if steps else None})
    return cur.fetchall()


def _advance(conn, link_id: int, step: str, app_id=None, payload=None):
    with conn.cursor() as cur:
        cur.execute("UPDATE link_prcs SET step_cd=%s, app_id=COALESCE(%s, app_id),"
                    " payload=COALESCE(%s::jsonb, payload), err_msg=NULL, updated_at=now()"
                    " WHERE link_id=%s",
                    (step, app_id, json.dumps(payload, ensure_ascii=False) if payload else None,
                     link_id))
    conn.commit()


def _handoff(conn, link_id: int, step: str):
    """담당 밖 단계 도달 — '대기'로 내려놓아 그 단계 담당 워커가 집어가게 한다."""
    with conn.cursor() as cur:
        cur.execute("UPDATE link_prcs SET step_cd=%s, stat_cd='대기', updated_at=now()"
                    " WHERE link_id=%s", (step, link_id))
    conn.commit()


def _finish(conn, link_id: int, ok: bool, err: str | None = None):
    with conn.cursor() as cur:
        if ok:
            cur.execute("UPDATE link_prcs SET step_cd='완료', stat_cd='완료',"
                        " err_msg=NULL, updated_at=now() WHERE link_id=%s", (link_id,))
        else:
            # err_msg에 실명·주민번호 기록 금지(개인정보 정책) — 예외 요약만 저장
            cur.execute("UPDATE link_prcs SET stat_cd='실패', retry_cnt=retry_cnt+1,"
                        " err_msg=%s, updated_at=now() WHERE link_id=%s",
                        ((err or "")[:500], link_id))
    conn.commit()


# ── 단계 구현 ────────────────────────────────────────────────────────────────
# 각 단계는 (conn, row, log) → payload 갱신분(dict)을 반환. 예외 발생 = 그 단계 실패.

def _step_collect(conn, row, log) -> dict:
    """수집: 원천 안건·요건·심의 텍스트를 내부 스키마로 적재 (컬럼 명세 260813 기반).
    LINK_SRC_DSN은 덤프 스테이징(PostgreSQL) 기준 — 원천(Oracle) 직결은 oracledb
    드라이버 반입 후 접속부만 교체. 첨부 실파일 반출은 미확정(RV_ATFL은 메타만) —
    파일 확보 전까지 VLM추출 단계는 기적재 스캔이 있는 경우만 수행된다."""
    key = row["src_case_key"]
    if LINK_SRC_DSN:
        from ingestion import link_collector as lc
        with psycopg.connect(LINK_SRC_DSN) as src, src.cursor() as scur:
            b = lc.fetch_case(scur, key)
        if not b.get("agnd"):
            raise RuntimeError(f"원천 안건 없음: {key} (RV_AGND)")
        app = lc.to_application(b)
        with conn.cursor() as cur:
            cur.execute("SELECT app_id FROM application WHERE src_case_key=%s", (key,))
            hit = cur.fetchone()
            if hit:
                app_id = hit[0]   # 재수집 — 상세 재적재는 생략(담당자 수정 보호)
            else:
                # 인적정보(성명 등)는 미수령 테이블 — 접수번호 기반 표시자, 수령 시 갱신
                cur.execute(
                    "INSERT INTO application(applicant, recv_no, src_case_key, agenda_no,"
                    " review_content, subcommittee, status, apply_story, duty_type)"
                    " VALUES (%s,%s,%s,%s,%s,%s,'접수',%s,%s) RETURNING app_id",
                    (f"(원천 {app.get('recv_no') or key})", app.get("recv_no"), key,
                     app.get("agenda_no"), app.get("review_content"),
                     app.get("subcommittee"), app.get("apply_story"), app.get("duty_type")))
                app_id = cur.fetchone()[0]
                for d in lc.to_disabilities(b):
                    cur.execute("INSERT INTO disability(app_id, name, onset_ym, onset_story,"
                                " fact_date, fact_place) VALUES (%s,%s,%s,%s,%s,%s)",
                                (app_id, d["name"] or "(미상)", d["onset_ym"],
                                 d["onset_story"], d.get("fact_date"), d.get("fact_place")))
                for s in lc.to_service_records(b):
                    cur.execute("INSERT INTO service_record(app_id, enlist_date,"
                                " discharge_date, career) VALUES (%s,%s,%s,%s)",
                                (app_id, _ymd(s["begin"]), _ymd(s["end"]),
                                 " / ".join(filter(None, [s["unit"], s["duty"], s["area"]]))))
        conn.commit()
        return {"app_id": app_id}
    files = sorted(str(p) for p in (Path(LINK_INBOX) / key).glob("*")
                   if p.suffix.lower() in (".pdf", ".txt"))
    if not files:
        # 수집함에 파일이 없어도 내부에 기적재된 안건이면 통과 (재처리 시나리오)
        with conn.cursor() as cur:
            cur.execute("SELECT app_id FROM application WHERE src_case_key=%s", (key,))
            hit = cur.fetchone()
        if hit:
            return {"app_id": hit[0]}
        raise RuntimeError(f"수집물 없음 — {LINK_INBOX}/{key}/ 비어 있고 내부 매핑도 없음")
    return {"files": files}


def _step_extract(conn, row, log) -> dict:
    """VLM추출: 텍스트층 있는 페이지는 추출만(EXTR_YN 스위치 — flow_ingest 내부 처리),
    없는 페이지만 VLM. 정규화 포함, 색인은 다음 단계."""
    files = (row.get("payload") or {}).get("files") or []
    if not files:
        return {}   # 기적재 안건 재처리 — 추출 생략
    from pipeline import flow_ingest
    r = flow_ingest.run(files, index=False, out_dir="out")
    return {"sd_ids": [x["sd_id"] for x in r["results"]]}


def _step_embed(conn, row, log) -> dict:
    """임베딩: 정리본 청킹 → pgvector 색인 (RAG·유사사례 검색 대상)."""
    from ingestion.embedder import get_embedder
    from services import ocr_normalize
    emb = get_embedder()
    for sd_id in (row.get("payload") or {}).get("sd_ids") or []:
        ocr_normalize.index_clean(sd_id, emb)
    return {}


def _step_draft(conn, row, log) -> dict:
    """초안생성: 안건 매핑(없으면 스캔→사건 변환 후 원천안건키 기록) → 의결서 초안.
    HITL — 판단 방향 확정·수정 권한은 담당자(flow_decision이 매 실행 표출)."""
    key, payload = row["src_case_key"], row.get("payload") or {}
    app_id = row.get("app_id") or payload.get("app_id")
    if not app_id:
        sd_ids = payload.get("sd_ids") or []
        if not sd_ids:
            raise RuntimeError("초안 대상 안건 없음 — 수집·추출 산출물과 내부 매핑 모두 부재")
        from services import scan_to_case
        r = scan_to_case.to_case(sd_ids[0])
        app_id = r.get("app_id")
        if not app_id:
            raise RuntimeError(f"스캔→사건 변환 실패: {r.get('error', '원인 미상')}")
    with conn.cursor() as cur:   # 원천안건키 ↔ 내부 안건 매핑 확정 (전송 시 대상 식별)
        cur.execute("UPDATE application SET src_case_key=%s"
                    " WHERE app_id=%s AND src_case_key IS NULL", (key, app_id))
    conn.commit()
    from pipeline import flow_decision
    flow_decision.run(app_id)
    return {"app_id": app_id}


def _step_send(conn, row, log) -> dict:
    """전송: 통합보훈 신규 테이블 insert. 쓰기 권한은 보훈부 협의 미결이라
    기본 hold — 우리 PG 보관(프론트는 API 조회). 협의 확정 시 db 모드 구현."""
    if LINK_SEND_MODE != "db":
        log.info("전송 보류(hold) — PG 보관, API 조회 경로 사용", app_id=row.get("app_id"))
        return {"send": "hold"}
    if not LINK_DEST_DSN:
        raise RuntimeError("LINK_SEND_MODE=db인데 LINK_DEST_DSN 미설정")
    raise RuntimeError("통합보훈 초안 테이블 규격 미확정 — _step_send의 insert 구현 필요"
                       " (신규 테이블 DDL·쓰기 권한 협의 대기)")


_STEPS = {"수집": _step_collect, "VLM추출": _step_extract,
          "임베딩": _step_embed, "초안생성": _step_draft, "전송": _step_send}


def process(conn, row: dict, log, steps: tuple[str, ...] | None = None) -> bool:
    """한 안건을 현재 단계부터 담당 범위 끝까지 진행 — 단계 성공마다 커밋(재개 지점 보존).
    steps 지정 시 담당 밖 단계에 도달하면 '대기'로 인계(다음 워커 몫)."""
    start = row["step_cd"] if row["step_cd"] in STEP_CHAIN else STEP_CHAIN[0]
    for step in STEP_CHAIN[STEP_CHAIN.index(start):]:
        if steps and step not in steps:
            _handoff(conn, row["link_id"], step)
            log.info(f"단계 인계 — {step}부터는 담당 워커 몫", link_id=row["link_id"])
            return True
        try:
            with log.stage(step, link_id=row["link_id"], key=row["src_case_key"]) as d:
                out = _STEPS[step](conn, row, log) or {}
                d.update(**{k: v for k, v in out.items() if isinstance(v, (int, str))})
        except Exception as e:
            conn.rollback()
            _finish(conn, row["link_id"], ok=False, err=f"{step}: {type(e).__name__}: {e}")
            log.warn(f"실패 — {step} 단계에서 중단(재시도 시 여기부터 재개)",
                     link_id=row["link_id"])
            return False
        payload = {**(row.get("payload") or {}), **out}
        row["payload"] = payload
        row["app_id"] = out.get("app_id") or row.get("app_id")
        nxt = STEP_CHAIN[STEP_CHAIN.index(step) + 1] if step != STEP_CHAIN[-1] else "완료"
        _advance(conn, row["link_id"], step if nxt == "완료" else nxt,
                 app_id=row.get("app_id"), payload=payload)
    _finish(conn, row["link_id"], ok=True)
    return True


# ── 순회·상주 ────────────────────────────────────────────────────────────────

def sweep_upload_inbox(log) -> int:
    """로컬 업로드 수신함(UPLOAD_INBOX) 일괄 처리 — 업무외시간 윈도우 안에서만 호출된다.
    성공 파일은 done/, 실패 파일은 fail/로 이동해 재처리·원인 확인이 쉽다.
    파일마다 윈도우를 재확인해 아침이 되면 즉시 멈춘다(남은 건 다음 야간)."""
    box = Path(UPLOAD_INBOX)
    if not box.is_dir():
        return 0
    files = sorted(p for p in box.iterdir()
                   if p.is_file() and p.suffix.lower() in (".pdf", ".txt"))
    if not files:
        return 0
    from pipeline import flow_ingest
    n = 0
    for f in files[:UPLOAD_SWEEP_MAX]:
        if not in_batch_window():
            break
        try:
            flow_ingest.run([str(f)])
            (box / "done").mkdir(exist_ok=True)
            f.rename(box / "done" / f.name)
            n += 1
        except Exception as e:
            log.warn(f"업로드 처리 실패 — {f.name}: {type(e).__name__}: {e}")
            (box / "fail").mkdir(exist_ok=True)
            f.rename(box / "fail" / f.name)
    return n


def sweep_minio_inbox(log) -> int:
    """MinIO 수신함(버킷의 UPLOAD_PREFIX 아래) 일괄 처리 — 대량 사전 적재(수십만 건) 경로.
    운영 절차: mc mirror 등으로 원본 PDF를 s3://<버킷>/inbox/ 에 밀어넣어 두면
    업무외시간마다 워커가 내려받아 전사·적재한다.
      성공: 원본은 파이프라인이 정식 경로(scans/<문서ID>/)에 재적재하므로 inbox 사본은 삭제
            (중복 보관으로 스토리지 2배가 되는 것을 방지)
      실패: inbox_fail/ 프리픽스로 이동해 원인 확인·재투입이 쉽게
    파일마다 윈도우를 재확인해 아침이 되면 즉시 멈춘다."""
    if not UPLOAD_PREFIX:
        return 0
    try:
        from core.storage import get_storage
        st = get_storage()
        if getattr(st, "backend", "local") != "minio":
            return 0
        objs = st.cli.list_objects(st.bucket, prefix=UPLOAD_PREFIX, recursive=True)
    except Exception as e:
        log.warn(f"MinIO 수신함 조회 실패 — {type(e).__name__}: {e}")
        return 0
    import shutil
    import tempfile
    from minio.commonconfig import CopySource
    from pipeline import flow_ingest
    n = 0
    for o in objs:
        if n >= UPLOAD_SWEEP_MAX or not in_batch_window():
            break
        key = o.object_name
        if key.endswith("/") or Path(key).suffix.lower() not in (".pdf", ".txt"):
            continue
        tmp_dir = tempfile.mkdtemp(prefix="inbox_")
        tmp = str(Path(tmp_dir) / Path(key).name)
        try:
            st.cli.fget_object(st.bucket, key, tmp)
            flow_ingest.run([tmp])
            st.cli.remove_object(st.bucket, key)   # 정식 경로 재적재 완료 — inbox 사본 정리
            n += 1
        except Exception as e:
            log.warn(f"MinIO 업로드 처리 실패 — {key}: {type(e).__name__}: {e}")
            try:
                fail_key = "inbox_fail/" + key[len(UPLOAD_PREFIX):]
                st.cli.copy_object(st.bucket, fail_key, CopySource(st.bucket, key))
                st.cli.remove_object(st.bucket, key)
            except Exception:
                pass   # 이동 실패 시 원위치 유지 — 다음 순회에서 재시도
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    return n


def run_once(out_dir: str = "out", steps: tuple[str, ...] | None = None) -> dict:
    """1회 순회: 폴링 적재 → 좀비 회수 → (윈도우 내) 업로드 수신함 → 픽업·처리.
    steps 지정 워커는 담당 단계만 픽업. 원천 폴링(신규 적재)은 수집 담당 워커만 수행
    (좀비 회수는 어느 워커든 — 큐 공통 관리).
    업무외시간 게이트: 윈도우 밖에서는 BATCH_STEPS(VLM추출·임베딩 등 무거운 단계)를
    픽업에서 제외한다 — 신규 안건의 수집·경량 단계는 상시, 무거운 처리는 업무 외 시간에."""
    log = RunLog("link", out_dir)
    polls = steps is None or STEP_CHAIN[0] in steps
    windowed = in_batch_window()
    eff = steps
    if BATCH_STEPS and not windowed:
        eff = tuple(s for s in (steps or STEP_CHAIN) if s not in BATCH_STEPS)
        if not eff:   # 담당 단계가 전부 배치 대상 — 이번 순회는 쉼
            log.done(스킵="업무시간(배치 윈도우 밖) — 담당 단계 전부 배치 대상")
            return {"run": str(log.path), "done": 0, "fail": 0, "gated": True}
    done = fail = 0
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn:
        with log.stage("폴링·회수", 담당=",".join(steps) if steps else "전체",
                       윈도우="배치가능" if windowed else "업무시간(무거운 단계 보류)") as d, \
                conn.cursor() as cur:
            d.update(신규적재=enqueue_new(cur) if polls else 0, 좀비회수=reap_zombies(cur))
            conn.commit()
        if polls and windowed:
            n_up = sweep_upload_inbox(log) + sweep_minio_inbox(log)
            if n_up:
                log.info(f"업로드 수신함 처리 {n_up}건",
                         local=UPLOAD_INBOX, minio_prefix=UPLOAD_PREFIX or "-")
        while True:
            eff = steps   # 픽업마다 게이트 재평가 — 긴 순회 중 윈도우가 닫히면 무거운 단계 즉시 보류
            if BATCH_STEPS and not in_batch_window():
                eff = tuple(s for s in (steps or STEP_CHAIN) if s not in BATCH_STEPS)
                if not eff:
                    break
            with conn.cursor() as cur:
                rows = pickup(cur, steps=eff)
                conn.commit()
            if not rows:
                break
            for row in rows:
                ok = process(conn, dict(row), log, steps=eff)
                done, fail = done + ok, fail + (not ok)
    log.done(완료=done, 실패=fail)
    return {"run": str(log.path), "done": done, "fail": fail}


def run(interval: int | None = None, out_dir: str = "out",
        steps: tuple[str, ...] | None = None):
    """상주 워커 — APScheduler 반입 시 사용, 미반입 환경은 sleep 루프 폴백(동작 동일).
    단계별 워커 분리 운용 예(같은 큐를 나눠 맡아 파이프라이닝):
      python -m pipeline link --steps 수집,VLM추출          # 파일 처리 워커
      python -m pipeline link --steps 임베딩,초안생성,전송   # 적재·생성 워커"""
    sec = interval or LINK_POLL_S
    kw = {"out_dir": out_dir, "steps": steps}
    tag = f" (담당: {','.join(steps)})" if steps else ""
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        sched = BlockingScheduler()
        sched.add_job(run_once, "interval", seconds=sec, kwargs=kw,
                      max_instances=1, coalesce=True, next_run_time=None)
        print(f"연계 워커 기동(APScheduler) — {sec}초 주기{tag}")
        run_once(**kw)   # 기동 직후 1회 즉시
        sched.start()
    except ImportError:
        print(f"연계 워커 기동(sleep 루프 — APScheduler 미반입) — {sec}초 주기{tag}")
        while True:
            run_once(**kw)
            time.sleep(sec)
