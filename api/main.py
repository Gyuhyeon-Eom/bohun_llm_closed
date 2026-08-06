"""FastAPI 진입점 - 기능②③⑤⑥. 화면/통합보훈시스템 연계는 이 API를 호출.

기동: uvicorn api.main:app --host 0.0.0.0 --port 8000
TODO(확인): 운영 전환 시 MockLLM -> FabrixClient, HashEmbedder -> bge로 교체
  (환경변수 EMBED_BACKEND=bge + 아래 _llm 한 줄)
"""
import json as _json
import os
import tempfile, threading, time
from pathlib import Path
from fastapi import FastAPI, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from ingestion.types import Block, BlockType
from ingestion.verifier import verify_blocks
from ingestion.chunker import chunk_blocks
from ingestion.indexer import index_document
from core.llm_client import RuleCorrectLLM
from pydantic import AliasChoices, BaseModel, Field
from core.llm_client import get_llm
from ingestion.embedder import get_embedder
from services import chatbot, similar_case, review_doc, stats, decision_doc, grade_predict

app = FastAPI(title="보훈심사 AI 지원", version="0.2")
_WEB = Path(__file__).parent.parent / "web"
app.mount("/img", StaticFiles(directory=_WEB / "img"), name="img")
app.mount("/css", StaticFiles(directory=_WEB / "css"), name="css")
app.mount("/js", StaticFiles(directory=_WEB / "js"), name="js")
_llm = get_llm()          # LLM_BACKEND=openai면 Ollama/FabriX, 기본은 mock

@app.middleware("http")
async def _no_stale_assets(request, call_next):
    """JS/CSS/HTML 캐시 재검증 강제 — 배포 후 브라우저가 옛 화면을 계속 보여주는 문제 방지.
    (ETag 304 재검증이라 트래픽 부담 없음 — 정적 파일이 바뀐 경우에만 재전송)"""
    resp = await call_next(request)
    path = request.url.path
    if path.startswith(("/js/", "/css/")) or path == "/" or path.endswith(".html"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


_emb = get_embedder()


# ── API 명세 v0.2 계약 별칭 (260803): ID는 DB정의서 v0.6 영문명·string 계약 ──
#   응답: 프로토타입 키(app_id 등)를 유지하면서 v0.6 키(aply_log_sn 등)를 string으로 병행 수록
#   요청: 바디의 v0.6 키를 pydantic 별칭으로 수용 (아래 각 모델 Field 참조)
_ID_ALIAS = {"app_id": "aply_log_sn", "dis_id": "wnd_sn", "ga_id": "grd_srng_sn",
             "sd_id": "orgtxt_dcmnt_sn", "cf_id": "orgtxt_dcmnt_sn",
             "cs_id": "chbt_sshn_sn", "session_id": "chbt_sshn_sn",
             "fb_id": "fdbk_sn", "parent_id": "up_sn", "case_id": "case_sn",
             "doc_id": "crps_doc_sn", "fe_id": "mdfcn_hstry_sn"}


def _add_id_aliases(obj):
    if isinstance(obj, dict):
        for k in list(obj.keys()):
            _add_id_aliases(obj[k])
            a = _ID_ALIAS.get(k)
            if a and a not in obj:
                v = obj[k]
                obj[a] = str(v) if isinstance(v, (int, float)) else v
    elif isinstance(obj, list):
        for it in obj:
            _add_id_aliases(it)


# 계약 API(명세 v0.7 — AI/LLM 처리)의 응답 봉투: {success, message, data} + 레거시 키 병행
import re as _re2

_ENVELOPE_RE = _re2.compile(
    r"^/(chatbot$|grade-predict$"
    r"|scan-docs/(upload$|\d+/(normalize|normalize-clear|index-clean|to-case|to-grade)$)"
    r"|decision-doc/\d+/(draft|judge)$"
    r"|grade-agendas/\d+/(draft|export)$"
    r"|cases/\d+/(similar$|similar-reasons$|files/ai-notes$))")


def _envelope(j):
    """success/message/data 봉투 — data가 정식 계약, 최상위 병행 키는 구화면 호환용."""
    if isinstance(j, list):
        return {"success": True, "message": "정상 처리되었습니다", "data": j}
    if isinstance(j, dict):
        ok = "error" not in j
        payload = {k: v for k, v in j.items() if k != "error"}
        return {"success": ok,
                "message": j.get("error") or "정상 처리되었습니다",
                "data": payload, **j}
    return j


@app.middleware("http")
async def _contract_id_aliases(request, call_next):
    resp = await call_next(request)
    if not resp.headers.get("content-type", "").startswith("application/json"):
        return resp
    body = b"".join([chunk async for chunk in resp.body_iterator])
    try:
        data = _json.loads(body)
        _add_id_aliases(data)
        if _ENVELOPE_RE.match(request.url.path):
            data = _envelope(data)
        body = _json.dumps(data, ensure_ascii=False, default=str).encode()
    except Exception:
        pass                            # JSON 아니거나 변형 실패 — 원본 그대로
    headers = dict(resp.headers)
    headers.pop("content-length", None)
    return Response(content=body, status_code=resp.status_code,
                    headers=headers, media_type="application/json")


# 요청 바디 ID는 각 모델에서 AliasChoices로 프로토타입 키·v0.6 키 둘 다 수용
# (pydantic v2 lax 모드라 "7" 같은 string 값도 int로 자동 변환됨 — 계약과 호환)


class Question(BaseModel):
    question: str
    only_uploaded: bool = False   # True면 UI로 넣은 문서만 검색
    history: list[dict] = []      # [{"role":"user"|"ai","text":...}] 최근 대화 (챗봇용)
    session_id: int | None = Field(None, validation_alias=AliasChoices("session_id", "chbt_sshn_sn"))
    # 우측 영역 패널 컨텍스트 — "현재 안건: 2026-0101 …" 상태로 질의 (안건 요약을 문맥 주입)
    app_id: int | None = Field(None, validation_alias=AliasChoices("app_id", "aply_log_sn"))
    ga_id: int | None = Field(None, validation_alias=AliasChoices("ga_id", "grd_srng_sn"))
    persist: bool = True          # False면 기록 저장 안 함 (AI 검토 패널 질의 등 일회성)


class IngestReq(BaseModel):
    """OCR 산출 텍스트/JSON 접수. 파일은 브라우저가 읽어 text로 보냄 (multipart 불필요)."""
    text: str
    filename: str = "붙여넣기"
    low_quality: bool = False     # True면 전 블록을 저신뢰(0.5)로 취급 -> 교정기 통과
    orig_name: str | None = None  # 원본 스캔 파일명 (PDF) — 출처 클릭 시 이 원본을 연다
    orig_b64: str | None = None   # 원본 스캔 파일 내용 (base64)


class SimilarReq(BaseModel):
    summary: str                      # 신청 건 요약문
    review_type: str | None = None    # 예: '요건심의'
    kcd_codes: list[str] | None = None
    n: int = 5


class ReviewDocReq(BaseModel):
    review_type: str                  # 예: '요건심의'
    review_content: str               # 예: '상이공무원심의'
    target_cond: str = ""             # 예: '' / '1개' / '2개 이상'
    kcd_codes: list[str] = []         # 신청 상이처 KCD
    facts: str                        # 사실관계 (자동추출 결과 또는 담당자 입력)


@app.get("/")
def ui():
    return FileResponse(_WEB / "index.html")


@app.get("/intake")                   # 구 화면: OCR 접수·질의 (프로토타입 유지)
def ui_intake():
    return FileResponse(_WEB / "intake.html")


def _parse_blocks(req: IngestReq) -> list[Block]:
    """우리 OCR JSON({"blocks":[...]})이면 그대로, 아니면 빈 줄 기준 문단 분해."""
    try:
        data = _json.loads(req.text)
        if isinstance(data, dict) and "blocks" in data:
            return [Block(BlockType(b.get("type", "paragraph")), b["text"], b.get("page", 1),
                          {"confidence": b.get("confidence", 1.0)}) for b in data["blocks"]]
    except (ValueError, KeyError):
        pass
    conf = 0.5 if req.low_quality else 1.0
    paras = [p.strip() for p in req.text.split("\n\n") if p.strip()]
    return [Block(BlockType.PARAGRAPH, p, i + 1, {"confidence": conf})
            for i, p in enumerate(paras)]


@app.post("/ingest")                  # 기능① 축소판: 접수->검증->청킹->임베딩->적재
def api_ingest(req: IngestReq):
    t0 = time.time()
    blocks = _parse_blocks(req)
    verified = verify_blocks(blocks, RuleCorrectLLM())
    n_corrected = sum(1 for b in verified if b.meta.get("verified"))
    chunks = chunk_blocks(verified)
    vecs = _emb.encode([c.content for c in chunks])
    # 원본 스캔 파일(PDF) 동봉 시 보관 — 출처 클릭 시 이 원본의 해당 페이지를 연다
    orig_path = None
    if req.orig_b64 and req.orig_name:
        import base64
        updir = Path(__file__).parent.parent / "data" / "uploads"
        updir.mkdir(parents=True, exist_ok=True)
        safe = "".join(c for c in req.orig_name if c not in '/\\:*?"<>|')
        orig = updir / f"{time.time_ns()}_{safe}"
        orig.write_bytes(base64.b64decode(req.orig_b64))
        orig_path = f"{orig}#{safe}"
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(req.text); tmp = f.name
    n = index_document(f"{tmp}#{req.filename}#{time.time_ns()}", "ui_upload", chunks, vecs, "ui",
                       orig_path=orig_path)
    return {"filename": req.filename, "blocks": len(blocks), "corrected": n_corrected,
            "chunks": n, "orig": bool(orig_path), "seconds": round(time.time() - t0, 2)}


# 동시 질의 현황 (개인 서버 시연용) — 로컬 LLM은 한 번에 한 건씩 생성하므로
# 진행 중 질의 수 = 대기 줄 길이. 화면이 이 값으로 "N명 사용 중" 안내를 띄운다.
_load_lock = threading.Lock()
_active_chats = 0


@app.get("/load")                     # 챗봇·AI검토 질의 동시 사용 현황
def api_load():
    return {"active": _active_chats}


def _case_context(app_id: int | None, ga_id: int | None) -> str | None:
    """우측 영역 '현재 안건' 문맥 — 챗봇 히스토리 선두에 주입할 안건 요약."""
    import psycopg
    from psycopg.rows import dict_row
    from config.settings import PG_DSN
    try:
        with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
            if app_id:
                cur.execute("SELECT recv_no, applicant, duty_type, review_content"
                            " FROM application WHERE app_id=%s", (app_id,))
                a = cur.fetchone()
                if not a:
                    return None
                cur.execute("SELECT name, body_side, kcd_code FROM disability"
                            " WHERE app_id=%s", (app_id,))
                dis = ", ".join(f"{d['name']}({d['body_side'] or '-'}·{d['kcd_code']})"
                                for d in cur.fetchall())
                return (f"[현재 안건] {a['recv_no']} {a['applicant']}({a['duty_type']})"
                        f" · {a['review_content']} · 신청상이: {dis}")
            if ga_id:
                cur.execute("SELECT agenda_no, person, exam_kind FROM grade_agenda"
                            " WHERE ga_id=%s", (ga_id,))
                g = cur.fetchone()
                if g:
                    return (f"[현재 안건] 상이등급 {g['agenda_no']} {g['person']}"
                            f" · {g.get('exam_kind') or ''}")
    except Exception:
        pass                           # 컨텍스트는 부가 정보 — 실패해도 질의는 진행
    return None


@app.post("/chatbot")                 # 기능② (성공 왕복은 세션 기록으로 저장)
def api_chatbot(q: Question):
    global _active_chats
    from core.llm_client import LLMUnavailable
    with _load_lock:
        _active_chats += 1
    try:
        ctx = _case_context(q.app_id, q.ga_id)
        if ctx:
            q.history = [{"role": "user", "text": ctx}] + (q.history or [])
        try:
            r = chatbot.answer(q.question, _llm, _emb,
                               doc_type="ui_upload" if q.only_uploaded else None,
                               history=q.history)
        except LLMUnavailable as e:
            return {"answer": None, "error": str(e), "sources": [], "session_id": q.session_id}
        except RuntimeError as e:
            return {"answer": None, "error": f"생성 실패: {e}", "sources": [], "session_id": q.session_id}
        sid = q.session_id
        if q.persist:
            try:
                sid = chatbot.save_exchange(q.session_id, q.question, r["answer"], r["sources"])
            except Exception:
                pass                   # 기록 실패는 답변 자체를 막지 않음 (DB 미가동 등)
        return {**r, "session_id": sid}
    finally:
        with _load_lock:
            _active_chats -= 1


@app.get("/source-doc/{doc_id}")      # 근거 원문 미리보기 (텍스트는 마스킹 본문 포함)
def api_source_doc(doc_id: int):
    from services import source_doc
    return source_doc.load(doc_id)


@app.get("/source-doc/{doc_id}/file") # 근거 원문 파일 (dl=1이면 다운로드, 아니면 인라인 — PDF #page 이동용)
def api_source_doc_file(doc_id: int, dl: int = 0):
    from services import source_doc
    r = source_doc.export_file(doc_id)
    if not r:
        return {"error": "원본 파일을 제공할 수 없습니다 (미등록·삭제·미지원 형식)"}
    fname, path, media = r
    return FileResponse(path, filename=fname, media_type=media,
                        content_disposition_type="attachment" if dl else "inline")


@app.get("/chat-sessions")            # 챗봇 과거기록: 세션 목록 (최근순)
def api_chat_sessions():
    return chatbot.list_sessions()


@app.get("/chat-sessions/{cs_id}")    # 세션 대화 전체 (이어보기·이어하기)
def api_chat_messages(cs_id: int):
    return chatbot.get_messages(cs_id)


@app.get("/llm-status")               # 챗봇·AI검토 화면의 연결 상태 표시용
def api_llm_status():
    """생성 LLM 도달 가능 여부. openai 백엔드면 모델 목록까지 2초 내 핑."""
    from config.settings import LLM_BACKEND, FABRIX_ENDPOINT, FABRIX_MODEL
    if LLM_BACKEND != "openai":
        return {"backend": LLM_BACKEND, "ok": False, "model": None,
                "detail": "mock 모드 - LLM_BACKEND=openai로 Ollama/FabriX 연동 필요"}
    import requests
    base = FABRIX_ENDPOINT.rsplit("/chat/completions", 1)[0]
    try:
        r = requests.get(f"{base}/models", timeout=2)
        r.raise_for_status()
        models = [m.get("id") for m in r.json().get("data", [])]
        ok = FABRIX_MODEL in models
        return {"backend": "openai", "ok": ok, "model": FABRIX_MODEL,
                "detail": "연결됨" if ok else
                f"서버 연결됨 - 모델 '{FABRIX_MODEL}' 미설치 (ollama pull {FABRIX_MODEL})",
                "available": models[:10]}
    except Exception as e:
        return {"backend": "openai", "ok": False, "model": FABRIX_MODEL,
                "detail": f"LLM 서버 응답 없음({base}) - ollama serve 실행 확인"}


def _paginate(rows: list, order: str | None, page: int | None, per_page: int | None,
              response=None) -> list:
    """목록 공통 정렬·페이징 (API 명세 v0.2) — 전체 건수는 X-Total-Count 헤더."""
    if order == "desc":
        rows = list(reversed(rows))
    if response is not None:
        response.headers["X-Total-Count"] = str(len(rows))
    if per_page:
        page = page or 1
        rows = rows[(page - 1) * per_page: page * per_page]
    return rows


@app.get("/cases")                    # 안건 목록 (사건 스키마: application) — 명세 v0.2 검색·페이징
def api_cases(response: Response = None, search_text: str | None = None,
              status: str | None = None, subcommittee: str | None = None,
              apply_kind: str | None = None, step: str | None = None,
              order: str | None = None, page: int | None = None,
              per_page: int | None = None):
    import psycopg
    from psycopg.rows import dict_row
    from config.settings import PG_DSN
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("""SELECT a.app_id, a.recv_no, a.applicant, a.duty_type, a.is_death,
                              a.review_content, a.subcommittee, a.round, a.status, a.apply_kind, a.track,
                              a.is_real, a.agenda_no, a.civil_receipt_date, a.rrn_masked, a.org,
                              a.assignee, a.team_lead, a.dept_head, a.assigned_date,
                              array_agg(d.name || COALESCE('('||d.body_side||')','')) AS dis_names,
                              array_agg(d.kcd_code) AS kcd_codes,
                              (SELECT count(*) FROM case_draft cd
                                WHERE cd.app_id=a.app_id AND cd.content IS NOT NULL AND cd.content<>'') AS n_draft,
                              count(d.dis_id) AS n_dis,
                              (SELECT count(*) FROM conclusion c
                                WHERE c.app_id=a.app_id AND c.round=a.round AND c.body_text IS NOT NULL) AS n_body,
                              (SELECT count(*) FROM conclusion c
                                WHERE c.app_id=a.app_id AND c.round=a.round AND c.status='확정') AS n_fixed
                       FROM application a LEFT JOIN disability d USING (app_id)
                       GROUP BY a.app_id ORDER BY a.app_id""")
        rows = cur.fetchall()
    # 목록 단계 표시 — 접수→작성→판단→확정 (요건심사 화면설계 260722: 목록에 진행단계 컬럼)
    for r in rows:
        if r["n_dis"] and r["n_fixed"] >= r["n_dis"]:
            r["step"] = "확정"
        elif r["n_body"]:
            r["step"] = "판단"
        elif r["n_draft"]:
            r["step"] = "작성"
        else:
            r["step"] = "접수"
    # 검색·필터 (명세 v0.2 — 소량 데이터라 파이썬 후처리로 충분, 운용 전환 시 SQL화)
    if search_text:
        t = search_text.strip()
        rows = [r for r in rows if any(t in str(r.get(k) or "") for k in
                ("recv_no", "agenda_no", "applicant")) or
                any(t in (d or "") for d in (r.get("dis_names") or []))]
    for key, val in (("status", status), ("subcommittee", subcommittee),
                     ("apply_kind", apply_kind), ("step", step)):
        if val:
            rows = [r for r in rows if str(r.get(key) or "") == val]
    return _paginate(rows, order, page, per_page, response)


@app.get("/cases/{app_id}")           # 안건 상세 (화면설계 BNM-U00-0100 — 인적사항+신청상이+진행)
def api_case_detail(app_id: int):
    """안건 단건 조회 — 명세 v0.3 신설. 목록 없이 안건 ID(aply_log_sn)로 직접 진입."""
    import psycopg
    from psycopg.rows import dict_row
    from config.settings import PG_DSN
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM application WHERE app_id=%s", (app_id,))
        app_row = cur.fetchone()
        if not app_row:
            return {"error": "안건 없음"}
        cur.execute("SELECT * FROM disability WHERE app_id=%s ORDER BY dis_id", (app_id,))
        diss = cur.fetchall()
        cur.execute("""SELECT dis_id, round, yeu_result, bosang_result, status, decided_at
                       FROM conclusion WHERE app_id=%s AND round=%s""",
                    (app_id, app_row.get("round")))
        concl = {c["dis_id"]: c for c in cur.fetchall()}
        cur.execute("SELECT count(*) FROM case_draft WHERE app_id=%s"
                    " AND content IS NOT NULL AND content<>''", (app_id,))
        n_draft = cur.fetchone()["count"]
    for d in diss:
        d["conclusion"] = concl.get(d["dis_id"])
    n_fixed = sum(1 for c in concl.values() if c["status"] == "확정")
    step = ("확정" if diss and n_fixed >= len(diss) else
            "판단" if concl else "작성" if n_draft else "접수")
    return {**app_row, "disabilities": diss, "n_draft": n_draft, "step": step}


@app.post("/cases/demo-seed")         # 정형화틀 기반 목데이터 6건 생성
def api_demo_seed():
    import mockgen.generate_cases as g
    g.main()
    import db.build_graph as bg
    bg.main()                          # 유사사례 그래프 재생성
    return api_cases()


class JudgeReq(BaseModel):
    # 명세 v0.8: 요청은 Y/N만 — 상이처 미지정 시 첫 상이처 (다상이 안건은 wnd_sn 지정)
    dis_id: int | None = Field(None, validation_alias=AliasChoices("dis_id", "wnd_sn"))
    yeu_result: str                    # 'Y'|'N' (해당|비해당 호환)
    bosang_result: str


class FinalizeReq(BaseModel):
    dis_id: int = Field(validation_alias=AliasChoices("dis_id", "wnd_sn"))
    body_text: str | None = None       # 담당자 수정본


@app.get("/decision-doc/export-batch")   # 선택 안건 의결서 일괄 zip (ids=1,2,3 / fmt=txt|pdf)
def api_decision_export_batch(ids: str, fmt: str = "txt"):
    try:
        app_ids = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError:
        return {"error": "ids 형식 오류 — 예: ids=1,2,3"}
    if not app_ids:
        return {"error": "선택된 안건이 없습니다"}
    if len(app_ids) > 50:
        return {"error": "일괄 산출은 최대 50건입니다"}
    if fmt == "pdf":
        try:
            fname, path = decision_doc.export_batch(app_ids, _emb, "pdf")
        except ModuleNotFoundError:
            return {"error": "PDF 생성 모듈(reportlab) 미설치 — pip install reportlab 후 서버 재시작"}
    else:
        fname, path = decision_doc.export_batch(app_ids, _emb, "txt")
    return FileResponse(path, filename=fname, media_type="application/zip")


@app.get("/decision-doc/{app_id}")    # 공통뼈대 1~4장 자료 패키지
def api_decision_doc(app_id: int):
    doc = decision_doc.build_doc(app_id, _emb)
    return doc or {"error": "안건 없음"}


@app.get("/decision-doc/{app_id}/export")   # 심의검토서 산출물 (fmt=txt|pdf|hwpx, dis_id=상이처 개별본)
def api_decision_export(app_id: int, fmt: str = "txt", dis_id: int | None = None):
    if fmt == "pdf":
        try:
            fname, path = decision_doc.export_pdf(app_id, _emb, dis_id)
        except ModuleNotFoundError:
            return {"error": "PDF 생성 모듈(reportlab) 미설치 — pip install reportlab 후 서버 재시작"}
        media = "application/pdf"
    elif fmt == "hwpx":
        # 검토의견 25·26: 한글 포맷 다운로드 — 표준 라이브러리 조립(반입 패키지 불필요)
        fname, path = decision_doc.export_hwpx(app_id, _emb, dis_id)
        media = "application/hwp+zip"
    else:
        fname, path = decision_doc.export_txt(app_id, _emb, dis_id)
        media = "text/plain; charset=utf-8"
    return FileResponse(path, filename=fname, media_type=media)


@app.get("/decision-doc/{app_id}/export-split")   # 상이처별 개별본 zip (상이처 여러 건 안건용)
def api_decision_export_split(app_id: int, fmt: str = "txt"):
    try:
        fname, path = decision_doc.export_split(app_id, _emb, fmt)
    except ValueError as e:
        return {"error": str(e)}
    except ModuleNotFoundError:
        return {"error": "PDF 생성 모듈(reportlab) 미설치 — pip install reportlab 후 서버 재시작"}
    return FileResponse(path, filename=fname, media_type="application/zip")


def _yn(v: str) -> str:
    """판단값 정규화 — 자바 백단은 Y/N 코드로 보냄 (DB에 있는 상세는 LLM 서버가 직접 조회)."""
    return {"Y": "해당", "N": "비해당", "y": "해당", "n": "비해당"}.get(v, v)


@app.post("/decision-doc/{app_id}/draft")     # AI 심의의결서 초안 생성 (화면설계 S3-① — 전체 란)
def api_decision_draft(app_id: int):
    """요청은 안건 ID만 — LLM 서버가 DB에서 자료를 읽어 s1~s3 란 초안을 생성·저장."""
    from services import case_draft
    from core.llm_client import LLMUnavailable
    sections, errors = [], []
    for section in ("s1", "s2", "s3"):
        try:
            r = case_draft.generate(app_id, section, get_llm(), _emb)
            if "error" in r:
                errors.append(f"{section}: {r['error']}")
            else:
                sections.append({"section": section, "content": r.get("content")})
        except LLMUnavailable as e:
            return {"error": str(e)}
        except Exception as e:
            errors.append(f"{section}: {e}")
    if not sections:
        return {"error": "; ".join(errors) or "초안 생성 실패"}
    # 관련자료 + 한줄요약 (명세 v0.9 — sections[].files): 안건 자료함에서 최종 자료 우선
    from services import case_file
    try:
        case_file.ai_notes(app_id, _llm)       # note 빈 행 한줄요약 생성 (mock이면 무시)
    except Exception:
        pass
    try:
        files = [{"file_id": str(f["cf_id"]), "filename": f["file_name"] or f["title"],
                  "page_no": None, "summary": (f.get("note") or "").removeprefix("[AI] ") or None}
                 for f in case_file.list_files(app_id)[:5]]
    except Exception:
        files = []
    for s in sections:
        s["files"] = files
    out = {"aply_log_sn": str(app_id), "sections": sections}
    if errors:
        out["partial"] = errors
    return out


@app.post("/decision-doc/{app_id}/judge")     # 이원 판단 선택 -> LLM 판단내용 생성·저장
def api_judge(app_id: int, req: JudgeReq):
    dis_id = req.dis_id
    if dis_id is None:                 # 명세 v0.8 최소 요청 — 첫 상이처 기본
        import psycopg
        from config.settings import PG_DSN
        with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
            cur.execute("SELECT dis_id FROM disability WHERE app_id=%s ORDER BY dis_id LIMIT 1",
                        (app_id,))
            row = cur.fetchone()
        if not row:
            return {"error": "안건에 상이처 없음"}
        dis_id = row[0]
    return decision_doc.draft_judgment(app_id, dis_id, _yn(req.yeu_result),
                                       _yn(req.bosang_result), _llm, _emb)


@app.post("/decision-doc/{app_id}/finalize")  # 담당자 수정 반영 + 확정
def api_finalize(app_id: int, req: FinalizeReq):
    return decision_doc.finalize(app_id, req.dis_id, req.body_text)


@app.get("/dashboard")                # 심사현황 (결정적 SQL - LLM 미사용)
def api_dashboard():
    import psycopg
    from psycopg.rows import dict_row
    from config.settings import PG_DSN
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT status, count(*)::int AS cnt FROM application GROUP BY 1 ORDER BY 1")
        by_status = cur.fetchall()
        cur.execute("""SELECT '제'||subcommittee||'분과' AS sub, count(*)::int AS cnt,
                              count(*) FILTER (WHERE status='의결')::int AS done
                       FROM application GROUP BY subcommittee ORDER BY subcommittee""")
        by_sub = cur.fetchall()
        cur.execute("""SELECT a.recv_no, a.applicant, d.name AS dis_name, c.final_text, c.decided_at
                       FROM conclusion c JOIN application a USING (app_id) JOIN disability d USING (dis_id)
                       WHERE c.status='확정' ORDER BY c.decided_at DESC NULLS LAST LIMIT 5""")
        recent = cur.fetchall()
    return {"by_status": by_status, "by_sub": by_sub, "recent": recent}


class GradePredictReq(BaseModel):
    # 안건 ID만 보내면 상이명·부위는 DB(grade_agenda.injury_items)에서 조회 — 자바 백단 최소 요청
    disease_name: str | None = None
    body_part: str | None = None
    n: int = 5
    ga_id: int | None = Field(None, validation_alias=AliasChoices("ga_id", "grd_srng_sn"))


class CaseFileReq(BaseModel):
    kind: str = "추가 자료"
    title: str
    dis_id: int | None = Field(None, validation_alias=AliasChoices("dis_id", "wnd_sn"))
    note: str | None = None


@app.get("/cases/{app_id}/files")             # 사건 자료함 (자동 파생 + 추가분, 최종 자료 우선)
def api_case_files(app_id: int):
    from services import case_file
    return case_file.list_files(app_id)


@app.post("/cases/{app_id}/files")            # 자료 메타 추가 (행 단위 — JSON append 불필요)
def api_case_file_add(app_id: int, req: CaseFileReq):
    from services import case_file
    return case_file.add(app_id, req.kind, req.title, req.dis_id, req.note)


class ReorderReq(BaseModel):
    ids: list[int]                     # 드래그 후 순서대로의 cf_id


@app.post("/cases/{app_id}/files/reorder")   # 자료 우선순위 저장 (260725 — 드래그 정렬)
def api_case_file_reorder(app_id: int, req: ReorderReq):
    from services import case_file
    return case_file.reorder(app_id, req.ids)


@app.post("/cases/{app_id}/files/ai-notes")   # 자료별 한 줄 AI 요약 (260724 — 왜 필요한 자료인지)
def api_case_file_ai_notes(app_id: int):
    from services import case_file
    return case_file.ai_notes(app_id, _llm)


@app.get("/cases/{app_id}/similar")           # 안건 기준 유사사례 (우측 영역 — 유사사례 검색 탭)
def api_case_similar(app_id: int, n: int = 10):   # 검토의견 38: 최소 5개 이상, 유사도순 최대한
    """안건 요약 임베딩으로 과거사례 검색 — 유사도(score)·사유(reason) 포함."""
    import psycopg
    from psycopg.rows import dict_row
    from config.settings import PG_DSN
    from services.similar_case import find_similar
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT review_content, apply_story FROM application WHERE app_id=%s",
                    (app_id,))
        a = cur.fetchone()
        if not a:
            return {"error": "안건 없음"}
        cur.execute("SELECT name, body_side, kcd_code FROM disability WHERE app_id=%s", (app_id,))
        dis = cur.fetchall()
        cur.execute("SELECT case_id, reason FROM sim_reason WHERE app_id=%s", (app_id,))
        reasons = {r["case_id"]: r["reason"] for r in cur.fetchall()}
    summary = (f"{a['review_content']} · "
               + ", ".join(f"{d['name']}({d['body_side'] or '-'})" for d in dis)
               + f" · {(a['apply_story'] or '')[:200]}")
    kcds = [d["kcd_code"] for d in dis if d["kcd_code"]]
    rows = find_similar(_emb.encode([summary])[0], kcd_codes=kcds or None, n=n)
    for r in rows:
        r["reason"] = reasons.get(r["case_id"])
    return rows


@app.get("/cases/{app_id}/history")           # 통합 작성이력 (우측 영역 — 작성이력 탭)
def api_case_history(app_id: int, limit: int = 50):
    """요건 의결서·상세 수정·확정·연계 상이등급 이벤트를 시간 역순 타임라인으로 통합."""
    import psycopg
    from psycopg.rows import dict_row
    from config.settings import PG_DSN
    events = []
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT section, source, updated_at FROM case_draft"
                    " WHERE app_id=%s AND content IS NOT NULL AND content<>''", (app_id,))
        titles = {"s1": "요건사실", "s2": "경위", "s3": "의학적 소견"}
        for r in cur.fetchall():
            events.append({"at": r["updated_at"], "area": "요건심사 심의 의결서",
                           "event": f"'{titles.get(r['section'], r['section'])}' 란 "
                                    + ("초안 생성" if r["source"] == "llm" else "수정 저장"),
                           "actor": "AI" if r["source"] == "llm" else "담당자"})
        cur.execute("SELECT field, editor, created_at FROM field_edit WHERE app_id=%s", (app_id,))
        for r in cur.fetchall():
            events.append({"at": r["created_at"], "area": "요건심사 상세",
                           "event": f"'{r['field']}' 항목 수정", "actor": r["editor"]})
        cur.execute("SELECT dis_id, status, decided_at FROM conclusion"
                    " WHERE app_id=%s AND decided_at IS NOT NULL", (app_id,))
        for r in cur.fetchall():
            events.append({"at": r["decided_at"], "area": "요건심사 심의 의결서",
                           "event": f"종합판단 {r['status']}", "actor": "담당자",
                           "dis_id": r["dis_id"]})
        cur.execute("""SELECT gl.step, gl.event, gl.actor, gl.created_at
                       FROM grade_log gl JOIN scan_doc sd ON sd.ga_id = gl.ga_id
                       WHERE sd.app_id=%s""", (app_id,))
        for r in cur.fetchall():
            events.append({"at": r["created_at"], "area": "상이등급심사",
                           "event": f"[{r['step']}] {r['event']}", "actor": r["actor"]})
    events = [e for e in events if e["at"] is not None]
    events.sort(key=lambda e: e["at"], reverse=True)
    return events[:limit]


@app.post("/cases/{app_id}/similar-reasons")  # 유사사례 'AI 왜 유사한지' 요약 생성·캐시
def api_similar_reasons(app_id: int):
    from services import sim_reason
    return sim_reason.generate(app_id, _llm, _emb)


@app.post("/cases/{app_id}/files/upload")     # 파일 업로드 추가
async def api_case_file_upload(app_id: int, file: UploadFile, kind: str = "추가 자료"):
    from services import case_file
    return case_file.save_upload(app_id, file.filename, await file.read(), kind)


@app.post("/case-files/{cf_id}/final")        # 최종 자료 지정/해제
def api_case_file_final(cf_id: int, is_final: int = 1):
    from services import case_file
    return case_file.set_final(cf_id, bool(is_final))


@app.get("/case-files/{cf_id}/download")      # 실물 파일 다운로드 (있을 때)
def api_case_file_download(cf_id: int):
    from services import case_file
    f = case_file.get_file(cf_id)
    if not f or not f.get("file_path") or not os.path.exists(f["file_path"]):
        return {"error": "실물 파일 없음 (메타 자료)"}
    return FileResponse(f["file_path"], filename=f["file_name"] or "file")


class DraftSaveReq(BaseModel):
    content: str
    editor: str = "담당자"


class DraftCheckReq(BaseModel):
    idx: int
    checked: bool


@app.get("/case-draft/{app_id}")             # 심의서 통합 작성 — 란별 초안·체크 상태
def api_case_draft(app_id: int):
    from services import case_draft
    return {"drafts": case_draft.get_all(app_id), "gate": case_draft.required_done(app_id)}


@app.post("/case-draft/{app_id}/{section}/generate")   # 란 초안 LLM 생성 (정형화틀 모듈 주입)
def api_case_draft_generate(app_id: int, section: str):
    from services import case_draft
    from core.llm_client import LLMUnavailable
    try:
        return case_draft.generate(app_id, section, get_llm(), _emb)
    except LLMUnavailable as e:
        return {"error": str(e)}


class DraftSaveAllReq(BaseModel):
    sections: dict                     # {"s1": "본문", "s2": "...", "s3": "..."} — 있는 란만
    editor: str = "담당자"


@app.post("/case-draft/{app_id}/save-all")  # 전체 심의의결서 저장 (화면설계 S4-③)
def api_case_draft_save_all(app_id: int, req: DraftSaveAllReq):
    from services import case_draft
    saved, errors = [], []
    for section, content in req.sections.items():
        r = case_draft.save(app_id, section, content or "", req.editor)
        (errors if "error" in r else saved).append(section)
    out = {"ok": not errors, "saved": saved}
    if errors:
        out["error"] = f"저장 실패 란: {', '.join(errors)} (section=s1|s2|s3)"
    return out


@app.post("/case-draft/{app_id}/{section}/save")        # 담당자 수정 저장 (교정쌍 축적)
def api_case_draft_save(app_id: int, section: str, req: DraftSaveReq):
    from services import case_draft
    return case_draft.save(app_id, section, req.content, req.editor)


@app.post("/case-draft/{app_id}/{section}/check")       # 란 체크리스트 토글
def api_case_draft_check(app_id: int, section: str, req: DraftCheckReq):
    from services import case_draft
    return case_draft.set_check(app_id, section, req.idx, req.checked)


@app.get("/decision-doc/{app_id}/export-assembled")     # 의결서 조립 산출 (LLM 미사용)
def api_export_assembled(app_id: int, fmt: str = "txt"):
    import tempfile
    from services import case_draft
    gate = case_draft.required_done(app_id)
    if not gate["ok"]:
        return {"error": "필수 체크리스트·란 작성 미완료", **gate}
    text = case_draft.assemble(app_id)
    if fmt == "pdf":
        from services.decision_doc import _text_to_pdf
        fname = f"심의검토서_조립_{app_id}.pdf"
        path = os.path.join(tempfile.gettempdir(), fname)
        _text_to_pdf(text, path)
        return FileResponse(path, filename=fname, media_type="application/pdf")
    if fmt == "hwpx":
        # 검토의견 25·26: 한글 포맷 — 표준 라이브러리 조립(services/hwpx_export.py)
        from services.hwpx_export import build_hwpx
        fname = f"심의검토서_조립_{app_id}.hwpx"
        path = os.path.join(tempfile.gettempdir(), fname)
        open(path, "wb").write(build_hwpx(f"심의검토서(안) — 안건 {app_id}", text))
        return FileResponse(path, filename=fname, media_type="application/hwp+zip")
    fname = f"심의검토서_조립_{app_id}.txt"
    path = os.path.join(tempfile.gettempdir(), fname)
    open(path, "w", encoding="utf-8").write(text)
    return FileResponse(path, filename=fname, media_type="text/plain; charset=utf-8")


class FieldEditReq(BaseModel):
    field: str
    value: str
    dis_id: int | None = Field(None, validation_alias=AliasChoices("dis_id", "wnd_sn"))
    editor: str = "담당자"


# 수정 허용 필드 화이트리스트 — 임의 컬럼 갱신 차단
_EDIT_APP_FIELDS = {"apply_story", "aftermath", "review_content"}
_EDIT_DIS_FIELDS = {"onset_story", "fact_date", "fact_place", "fact_first_dx"}


@app.post("/cases/{app_id}/field")    # 항목 수정 (텍스트박스 편집 저장 + 교정쌍 축적)
def api_edit_field(app_id: int, req: FieldEditReq):
    import psycopg
    from config.settings import PG_DSN
    if req.dis_id is None and req.field not in _EDIT_APP_FIELDS:
        return {"error": f"수정 불가 필드: {req.field}"}
    if req.dis_id is not None and req.field not in _EDIT_DIS_FIELDS:
        return {"error": f"수정 불가 필드: {req.field}"}
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        if req.dis_id is None:
            cur.execute(f"SELECT {req.field} FROM application WHERE app_id=%s", (app_id,))
            row = cur.fetchone()
            if not row:
                return {"error": "안건 없음"}
            old = row[0]
            cur.execute(f"UPDATE application SET {req.field}=%s WHERE app_id=%s",
                        (req.value, app_id))
        else:
            cur.execute(f"SELECT {req.field} FROM disability WHERE dis_id=%s AND app_id=%s",
                        (req.dis_id, app_id))
            row = cur.fetchone()
            if not row:
                return {"error": "상이처 없음"}
            old = row[0]
            cur.execute(f"UPDATE disability SET {req.field}=%s WHERE dis_id=%s",
                        (req.value, req.dis_id))
        cur.execute("INSERT INTO field_edit(app_id, dis_id, field, old_value, new_value, editor)"
                    " VALUES (%s,%s,%s,%s,%s,%s)",
                    (app_id, req.dis_id, req.field, old, req.value, req.editor))
        conn.commit()
    return {"ok": True, "field": req.field}


@app.get("/field-edits/{app_id}")     # 수정 이력 (교정 학습 축적분 확인)
def api_field_edits(app_id: int):
    import psycopg
    from psycopg.rows import dict_row
    from config.settings import PG_DSN
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM field_edit WHERE app_id=%s ORDER BY fe_id DESC LIMIT 50",
                    (app_id,))
        return cur.fetchall()


class SimilarPickReq(BaseModel):
    scope: str                 # case | grade
    case_id: int = Field(validation_alias=AliasChoices("case_id", "case_sn", "trgt_case_sn"))
    kind: str                  # exclude | pin | clear
    app_id: int | None = Field(None, validation_alias=AliasChoices("app_id", "aply_log_sn"))
    dis_id: int | None = Field(None, validation_alias=AliasChoices("dis_id", "wnd_sn"))
    ga_id: int | None = Field(None, validation_alias=AliasChoices("ga_id", "grd_srng_sn"))
    weight: float = 1.0
    note: str | None = None


@app.post("/similar-picks")           # 유사사례 제외/추가·가중치 (260721 회의 ③)
def api_similar_pick(req: SimilarPickReq):
    from services import similar_pick
    return similar_pick.set_pick(req.scope, req.case_id, req.kind, req.app_id,
                                 req.dis_id, req.ga_id, req.weight, req.note)


@app.get("/similar-picks")            # 현재 선별 상태 조회
def api_similar_picks(scope: str, app_id: int | None = None,
                      dis_id: int | None = None, ga_id: int | None = None):
    from services import similar_pick
    return similar_pick.get_picks(scope, app_id, dis_id, ga_id)


@app.get("/case-pool/{case_id}")      # 과거사례 상세 (유사사례 클릭 팝업 — 260725)
def api_case_pool(case_id: int):
    import psycopg
    from psycopg.rows import dict_row
    from config.settings import PG_DSN
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("""SELECT case_id, review_type, review_content, exam_category,
                              kcd_codes, decision, decided_at, summary
                       FROM cases WHERE case_id=%s""", (case_id,))
        return cur.fetchone() or {"error": "사례 없음"}


@app.get("/cases-search")             # 위원 직접 추가용 사례 검색 (요약문·KCD)
def api_cases_search(q: str, n: int = 10):
    from services import similar_pick
    return similar_pick.search_cases(q, n)


@app.get("/grade-agendas")            # 상이등급심사 안건 목록 (화면 v0.4) — 명세 v0.2 검색·페이징
def api_grade_agendas(response: Response = None, search_text: str | None = None,
                      progress: str | None = None, order: str | None = None,
                      page: int | None = None, per_page: int | None = None):
    import json as _j
    import psycopg
    from psycopg.rows import dict_row
    from config.settings import PG_DSN
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM grade_agenda ORDER BY ga_id")
        rows = cur.fetchall()
    if search_text:
        t = search_text.strip()
        rows = [r for r in rows if t in str(r.get("agenda_no") or "")
                or t in str(r.get("person") or "")
                or t in _j.dumps(r.get("injury_items") or [], ensure_ascii=False)]
    if progress:
        rows = [r for r in rows if str(r.get("progress") or "") == progress]
    return _paginate(rows, order, page, per_page, response)


@app.get("/grade-agendas/export-batch")  # 선택 안건 심사표 일괄 zip (ids=1,2,3) — {ga_id} 라우트보다 먼저 선언
def api_grade_export_batch(ids: str):
    try:
        ga_ids = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError:
        return {"error": "ids 형식 오류 — 예: ids=1,2,3"}
    if not ga_ids:
        return {"error": "선택된 안건이 없습니다"}
    if len(ga_ids) > 50:
        return {"error": "일괄 산출은 최대 50건입니다"}
    from services import grade_export
    fname, path = grade_export.export_batch(ga_ids, emb=_emb)
    return FileResponse(path, filename=fname, media_type="application/zip")


@app.get("/grade-agendas/{ga_id}")    # 안건 상세 (신검과목·검토사항·비고)
def api_grade_agenda(ga_id: int):
    import psycopg
    from psycopg.rows import dict_row
    from config.settings import PG_DSN
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM grade_agenda WHERE ga_id=%s", (ga_id,))
        row = cur.fetchone()
        return row or {"error": "안건 없음"}


@app.post("/grade-predict")           # AI 판정예측 (과거 등급사례 기반 참고 예측)
def api_grade_predict(req: GradePredictReq):
    disease, body = req.disease_name, req.body_part
    if not disease and req.ga_id:      # ID만 받은 요청 — 상이명은 DB에서 (자바 백단 최소 요청)
        import psycopg
        from psycopg.rows import dict_row
        from config.settings import PG_DSN
        from services.grade_export import _items
        with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM grade_agenda WHERE ga_id=%s", (req.ga_id,))
            ag = cur.fetchone()
        if not ag:
            return {"error": "안건 없음"}
        items = _items(dict(ag))
        if not items or not items[0].get("injury"):
            return {"error": "심사표에 상이처가 없음 — disease_name을 직접 지정하세요"}
        disease, body = items[0]["injury"], items[0].get("body_part")
    if not disease:
        return {"error": "disease_name 또는 grd_srng_sn 필요"}
    return grade_predict.predict(disease, body, _emb, req.n, req.ga_id)


@app.get("/grade-agendas/{ga_id}/log")   # 안건 작업로그 (DAG 노드·이벤트)
def api_grade_log(ga_id: int):
    import psycopg
    from psycopg.rows import dict_row
    from config.settings import PG_DSN
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT progress, assignee FROM grade_agenda WHERE ga_id=%s", (ga_id,))
        head = cur.fetchone() or {}
        cur.execute("SELECT step, event, actor, detail, file_name, status,"
                    " to_char(created_at,'YYYY-MM-DD HH24:MI') AS created_at"
                    " FROM grade_log WHERE ga_id=%s ORDER BY gl_id", (ga_id,))
        return {"progress": head.get("progress"), "assignee": head.get("assignee"),
                "steps": ["접수", "자료수집", "AI예측", "검토", "의결", "완료"],
                "logs": cur.fetchall()}


class GradeItemsReq(BaseModel):
    items: list[dict]                  # 심사표 편집분 (상이처별 행)


@app.post("/grade-agendas/{ga_id}/items")   # 심사표 상이처별 값 수정 저장 (화면설계 260722)
def api_grade_items_save(ga_id: int, req: GradeItemsReq):
    """상세 화면의 수정가능 심사표 저장 — injury_items(JSONB) 갱신.
    화이트리스트 키만 반영하고, 담당자 확정 제안등급(proposed_grade)은 XLSX 산출에도 우선 적용된다."""
    import json
    import psycopg
    from psycopg.rows import dict_row
    from config.settings import PG_DSN
    from services.grade_export import _items
    ALLOW = ("injury", "body_part", "prev_grade", "exam_dept", "exam_grade",
             "proposed_grade", "opinion")
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM grade_agenda WHERE ga_id=%s", (ga_id,))
        ag = cur.fetchone()
        if not ag:
            return {"error": "안건 없음"}
        cur_items = _items(dict(ag))
        for i, inc in enumerate(req.items):
            if i >= len(cur_items):
                cur_items.append({})
            for k in ALLOW:
                if k in inc:
                    v = inc[k]
                    cur_items[i][k] = (str(v).strip() or None) if v is not None else None
        cur_items = cur_items[:max(len(req.items), 1)]   # 행 삭제 반영 (최소 1행 유지)
        cur.execute("UPDATE grade_agenda SET injury_items=%s::jsonb, updated_at=now() WHERE ga_id=%s",
                    (json.dumps(cur_items, ensure_ascii=False), ga_id))
        cur.execute("INSERT INTO grade_log(ga_id, step, event, actor, detail, status)"
                    " VALUES (%s,%s,%s,%s,%s,'done')",
                    (ga_id, ag.get("progress") or "검토", "심사표 수정", "담당자",
                     f"상이처별 행 {len(cur_items)}건 저장 (제안등급 확정값은 XLSX에 우선 반영)"))
        conn.commit()
    return {"ok": True, "items": cur_items}


class GradeLogReq(BaseModel):
    step: str
    event: str
    actor: str | None = "담당자"
    detail: str | None = None
    file_name: str | None = None
    status: str | None = "done"
    advance: bool | None = False


@app.post("/grade-agendas/{ga_id}/log")   # 작업로그 자동 기록 (프론트 이벤트)
def api_grade_log_add(ga_id: int, req: GradeLogReq):
    import psycopg
    from config.settings import PG_DSN
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO grade_log(ga_id, step, event, actor, detail, file_name, status)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (ga_id, req.step, req.event, req.actor, req.detail, req.file_name, req.status))
        if req.advance:
            cur.execute("UPDATE grade_agenda SET progress=%s, updated_at=now() WHERE ga_id=%s",
                        (req.step, ga_id))
    return {"ok": True}


@app.post("/grade-agendas/{ga_id}/draft")   # 상이등급 AI 심의의결서 생성 (명세 v0.9 — S6-②)
def api_grade_draft(ga_id: int):
    """심사표 상이처별 제안등급·심사의견을 AI 판정예측으로 채워 저장 — 담당자 수정 전제."""
    import json as _j
    import psycopg
    from psycopg.rows import dict_row
    from config.settings import PG_DSN
    from services.grade_export import _items
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM grade_agenda WHERE ga_id=%s", (ga_id,))
        ag = cur.fetchone()
        if not ag:
            return {"error": "안건 없음"}
        items = _items(dict(ag))
        if not items:
            return {"error": "심사표에 상이처가 없음"}
        filled = []
        for it in items:
            injury = (it.get("injury") or "").strip()
            if injury and not (it.get("proposed_grade") and it.get("opinion")):
                p = grade_predict.predict(injury, it.get("body_part"), _emb, 5, ga_id)
                if p.get("grade1"):
                    it.setdefault("proposed_grade", None)
                    if not it.get("proposed_grade"):
                        it["proposed_grade"] = p["grade1"]
                    if not it.get("opinion"):
                        it["opinion"] = p.get("rationale")
            filled.append({k: it.get(k) for k in
                           ("injury", "body_part", "exam_dept", "proposed_grade", "opinion")})
        cur.execute("UPDATE grade_agenda SET injury_items=%s::jsonb, updated_at=now()"
                    " WHERE ga_id=%s", (_j.dumps(items, ensure_ascii=False), ga_id))
        cur.execute("INSERT INTO grade_log(ga_id, step, event, actor, detail, status)"
                    " VALUES (%s,'검토','AI 심의의결서 작성','AI',%s,'done')",
                    (ga_id, f"상이처 {len(filled)}건 제안등급·심사의견 생성"))
        conn.commit()
    return {"ga_id": str(ga_id), "items": filled,
            "note": "AI 작성분은 참고용 — 확정은 담당자·심의 의결"}


@app.get("/grade-agendas/{ga_id}/export")  # 상이등급 심사표 산출 — 기본 봉투 JSON+URL, dl=1 파일 스트림
def api_grade_export(ga_id: int, dl: int = 0):
    """명세 v0.10: 프론트는 파일 스트림 대신 봉투 JSON을 받는다(260803 프론트 요청).
    기본 응답 = {success, message, data:{file_name, url, expires_s}} — url을 열면 다운로드.
    dl=1은 그 url이 가리키는 실제 파일 스트림(브라우저 링크용, 프론트가 직접 파싱하지 않음)."""
    import psycopg
    from config.settings import PG_DSN
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM grade_agenda WHERE ga_id=%s", (ga_id,))
        if not cur.fetchone():
            return {"error": "해당 등급 안건 없음"}
    from services import grade_export
    fname, path = grade_export.export_xlsx(ga_id, emb=_emb)
    if dl:
        return FileResponse(path, filename=fname,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    # 산출본 보관(스토리지) + grade_log 기록 — '이전 심사 기록 엑셀 확인' 메뉴의 원천
    url, backend, expires = f"/grade-agendas/{ga_id}/export?dl=1", "local", None
    try:
        from core.storage import get_storage
        st = get_storage()
        key = f"exports/grade/{ga_id}/{fname}"
        st.put_file(key, path)
        if st.backend == "minio":
            from config.settings import PRESIGNED_EXPIRES_S
            url, backend, expires = st.presigned_url(key), "minio", PRESIGNED_EXPIRES_S
    except Exception:
        pass                      # 보관 실패해도 dl=1 즉석 생성 URL은 항상 유효
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO grade_log(ga_id, step, event, actor, detail, file_name, status)"
                    " VALUES (%s,'산출','심사표 엑셀 산출','AI','심사표 xlsx 생성·보관',%s,'done')",
                    (ga_id, fname))
        conn.commit()
    return {"ga_id": str(ga_id), "file_name": fname, "url": url,
            "backend": backend, "expires_s": expires}


@app.get("/rule-check/{app_id}")      # 분과 판단기준 자동대조 (정형화틀 v2.4, 결정적)
def api_rule_check(app_id: int):
    from services import rule_check
    return rule_check.check(app_id)


@app.get("/scan-docs")                # 스캔 의무기록 목록 (OCR 적재분)
def api_scan_docs():
    import psycopg
    from psycopg.rows import dict_row
    from config.settings import PG_DSN
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT sd_id, reg_no, person, sex_age, hospital, doc_kind, file_name,"
                    " pages, ocr_used, jsonb_array_length(coalesce(exams,'[]'::jsonb)) AS n_exams,"
                    " (SELECT count(*) FROM jsonb_array_elements(coalesce(exams,'[]'::jsonb)) b"
                    "   WHERE b ? 'norm') AS n_norm,"          # 정규화 완료 블록 수 (OCR 페이지 상태 칩)
                    " app_id, is_real, created_at FROM scan_doc ORDER BY sd_id DESC")
        return cur.fetchall()


@app.get("/scan-docs/{sd_id}")        # 스캔 문서 상세 (파싱된 검사 블록 + 원문)
def api_scan_doc(sd_id: int):
    import psycopg
    from psycopg.rows import dict_row
    from config.settings import PG_DSN
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT sd_id, reg_no, person, sex_age, hospital, doc_kind, file_name,"
                    " pages, ocr_used, exams, app_id, is_real, raw_text, created_at"
                    " FROM scan_doc WHERE sd_id=%s", (sd_id,))
        row = cur.fetchone()
        return row or {"error": "스캔 문서 없음"}


class ScanUploadReq(BaseModel):
    """OCR 완료 txt 업로드 — 브라우저가 파일을 읽어 text로 보냄 (IngestReq와 동일 방식).
    저장 원문은 ingest_real_txt가 주민번호를 마스킹하며, 원본 파일은 data/originals/scans/에 보존."""
    filename: str
    text: str


@app.post("/scan-docs/upload")        # OCR txt 업로드 → 하위문서 분할 파싱 → scan_doc 적재
def api_scan_upload(req: ScanUploadReq):
    import unicodedata
    from scripts.ocr_ingest_scans import ingest_real_txt
    fname = os.path.basename(unicodedata.normalize("NFC", req.filename)) or "업로드.txt"
    if not fname.lower().endswith(".txt"):
        return {"error": "txt 파일만 업로드 가능 (스캔 PDF는 scripts/ocr_ingest_scans.py 사용)"}
    updir = Path("data") / "scans_txt"
    updir.mkdir(parents=True, exist_ok=True)
    path = updir / fname
    path.write_text(req.text, encoding="utf-8")
    try:
        sd_id, person, disease, nblk = ingest_real_txt(str(path))
    except Exception as e:
        return {"error": f"적재 실패: {e}"}
    return {"sd_id": sd_id, "person": person, "disease": disease, "blocks": nblk}


@app.get("/scan-docs/{sd_id}/file")   # 스캔 원본 PDF (열람·다운로드)
def api_scan_doc_file(sd_id: int, dl: int = 0):
    import psycopg
    from config.settings import PG_DSN
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT orig_path, file_name, obj_key FROM scan_doc WHERE sd_id=%s", (sd_id,))
        row = cur.fetchone()
    # MinIO 원본이면 presigned URL 302 — 브라우저 #page= 프래그먼트는 리다이렉트에도 유지됨
    if row and row[2] and not dl:
        from core.storage import get_storage
        st = get_storage()
        if st.backend == "minio" and st.exists(row[2]):
            from fastapi.responses import RedirectResponse
            return RedirectResponse(st.presigned_url(row[2]), status_code=302)
    if not row or not row[0] or not os.path.exists(row[0]):
        return {"error": "원본 파일 없음"}
    disp = "attachment" if dl else "inline"
    media = ("text/plain; charset=utf-8" if row[0].lower().endswith(".txt")
             else "application/pdf")
    return FileResponse(row[0], filename=row[1], media_type=media,
                        content_disposition_type=disp)


@app.get("/scan-docs/{sd_id}/url")    # 원본 열람 URL 발급 (minio=presigned, local=앱 경로)
def api_scan_doc_url(sd_id: int, page: int = 0):
    import psycopg
    from config.settings import PG_DSN
    from core.storage import get_storage
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT obj_key FROM scan_doc WHERE sd_id=%s", (sd_id,))
        row = cur.fetchone()
    st = get_storage()
    if row and row[0] and st.backend == "minio" and st.exists(row[0]):
        from config.settings import PRESIGNED_EXPIRES_S
        return {"url": st.presigned_url(row[0], page or None), "backend": "minio",
                "expires_s": PRESIGNED_EXPIRES_S}
    frag = f"#page={page}" if page else ""
    return {"url": f"/scan-docs/{sd_id}/file{frag}", "backend": "local", "expires_s": None}


@app.get("/scan-docs/{sd_id}/pages")  # 페이지 처리상태 (file_page — 텍스트층/OCR/검수/반영)
def api_scan_doc_pages(sd_id: int):
    import psycopg
    from psycopg.rows import dict_row
    from config.settings import PG_DSN
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT page_no, txt_layer, ocr_done, reviewed, applied, confidence"
                    " FROM file_page WHERE sd_id=%s ORDER BY page_no", (sd_id,))
        return {"sd_id": sd_id, "pages": cur.fetchall()}


class PageStateReq(BaseModel):
    reviewed: bool | None = None
    applied: bool | None = None


@app.post("/scan-docs/{sd_id}/pages/{page_no}")  # 페이지 검수/반영 상태 갱신 (담당자)
def api_scan_doc_page_update(sd_id: int, page_no: int, req: PageStateReq):
    import psycopg
    from config.settings import PG_DSN
    sets, vals = [], []
    if req.reviewed is not None:
        sets.append("reviewed=%s"); vals.append(req.reviewed)
    if req.applied is not None:
        sets.append("applied=%s"); vals.append(req.applied)
    if not sets:
        return {"error": "갱신할 상태 없음"}
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE file_page SET {', '.join(sets)} WHERE sd_id=%s AND page_no=%s",
                    (*vals, sd_id, page_no))
        conn.commit()
        if cur.rowcount == 0:
            return {"error": "해당 페이지 없음"}
    return {"ok": True, "sd_id": sd_id, "page_no": page_no}


@app.post("/scan-docs/{sd_id}/to-case")   # 스캔 문서 → 요건심사 사건 변환 (HITL 전제)
def api_scan_to_case(sd_id: int):
    from services import scan_to_case
    return scan_to_case.to_case(sd_id)


@app.post("/scan-docs/to-grade-all")      # 미변환 실데이터 전체 → 상이등급 안건 일괄 변환
def api_scan_to_grade_all():
    import psycopg
    from config.settings import PG_DSN
    from services import scan_to_case
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT sd_id, person FROM scan_doc WHERE is_real AND ga_id IS NULL ORDER BY sd_id")
        targets = cur.fetchall()
    done, skipped = [], []
    for sd_id, person in targets:
        try:
            r = scan_to_case.to_grade(sd_id)
            (skipped if "error" in r else done).append(
                {"sd_id": sd_id, "person": person, **({"error": r["error"]} if "error" in r
                 else {"ga_id": r["ga_id"], "agenda_no": r.get("agenda_no"), "existed": r.get("existed", False)})})
        except Exception as e:
            skipped.append({"sd_id": sd_id, "person": person, "error": str(e)[:120]})
    return {"converted": len(done), "skipped": len(skipped), "done": done, "errors": skipped}


@app.post("/scan-docs/{sd_id}/to-grade")  # 스캔 문서 → 상이등급 안건 변환 (신검 서류)
def api_scan_to_grade(sd_id: int):
    from services import scan_to_case
    return scan_to_case.to_grade(sd_id)


@app.post("/scan-docs/{sd_id}/normalize")  # OCR 텍스트 LLM 정규화 (260721 회의 반영)
def api_scan_normalize(sd_id: int, force: int = 0, limit: int | None = None):
    # limit: 블록 단위 스텝 실행 — UI가 진행률(remaining)을 보며 반복 호출.
    # 재정규화 UI는 force 대신 normalize-clear 후 스텝한다 (force+limit는 진행 불가 조합)
    from services import ocr_normalize
    return ocr_normalize.normalize_scan(sd_id, force=bool(force), limit=limit)


@app.post("/scan-docs/{sd_id}/normalize-clear")  # 정규화 결과 제거 (재정규화 스텝 실행 준비)
def api_scan_normalize_clear(sd_id: int):
    from services import ocr_normalize
    return ocr_normalize.clear_norms(sd_id)


@app.post("/scan-docs/{sd_id}/index-clean")   # 정리본 → RAG 적재 (챗봇 검색 대상화)
def api_scan_index_clean(sd_id: int):
    from services import ocr_normalize
    return ocr_normalize.index_clean(sd_id, _emb)


@app.post("/scan-docs/index-clean-all")       # 실데이터 전체 정리본 일괄 RAG 적재
def api_scan_index_clean_all():
    import psycopg
    from config.settings import PG_DSN
    from services import ocr_normalize
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT sd_id FROM scan_doc WHERE is_real ORDER BY sd_id")
        ids = [r[0] for r in cur.fetchall()]
    done, errors = [], []
    for sd_id in ids:
        try:
            r = ocr_normalize.index_clean(sd_id, _emb)
            (errors if "error" in r else done).append({"sd_id": sd_id, **r})
        except Exception as e:
            errors.append({"sd_id": sd_id, "error": str(e)[:120]})
    return {"indexed": sum(1 for d in done if not d.get("skipped")),
            "skipped": sum(1 for d in done if d.get("skipped")),
            "errors": errors}


@app.get("/scan-docs/{sd_id}/clean")       # 정리본(JSON) — 정규화 결과의 열람용 결정적 조립
def api_scan_clean(sd_id: int):
    from services import ocr_normalize
    return ocr_normalize.clean_document(sd_id) or {"error": "스캔 문서 없음"}


@app.get("/scan-docs/{sd_id}/clean.pdf")   # 정리본(PDF) — 열람·출력용
def api_scan_clean_pdf(sd_id: int, dl: int = 0):
    from services import ocr_normalize
    from services.decision_doc import _text_to_pdf
    doc = ocr_normalize.clean_document(sd_id)
    if not doc:
        return {"error": "스캔 문서 없음"}
    out = Path("data") / "clean_pdf"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"scan_{sd_id}_clean.pdf"
    _text_to_pdf(ocr_normalize.clean_text(doc), str(path))
    return FileResponse(str(path), filename=f"정리본_{sd_id}.pdf", media_type="application/pdf",
                        content_disposition_type="attachment" if dl else "inline")


@app.get("/grade-agendas/{ga_id}/scan")   # 등급 안건에 연결된 스캔 원문·정규화 결과 (근거 추적)
def api_grade_scan(ga_id: int):
    import psycopg
    from psycopg.rows import dict_row
    from config.settings import PG_DSN
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT sd_id, person, hospital, doc_kind, file_name, pages, exams"
                    " FROM scan_doc WHERE ga_id=%s ORDER BY sd_id", (ga_id,))
        return cur.fetchall()


class FeedbackReq(BaseModel):
    author: str = "익명"
    content: str
    parent_id: int | None = Field(None, validation_alias=AliasChoices("parent_id", "up_sn"))


@app.get("/feedback")                 # T/F 피드백 게시판: 글+답글 트리
def api_feedback_list():
    import psycopg
    from psycopg.rows import dict_row
    from config.settings import PG_DSN
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("""SELECT fb_id, parent_id, author, content,
                              to_char(created_at, 'YYYY-MM-DD HH24:MI') AS created_at
                       FROM feedback ORDER BY fb_id""")
        rows = cur.fetchall()
    posts = [dict(r, comments=[]) for r in rows if r["parent_id"] is None]
    by_id = {p["fb_id"]: p for p in posts}
    for r in rows:
        if r["parent_id"] and r["parent_id"] in by_id:
            by_id[r["parent_id"]]["comments"].append(r)
    posts.reverse()                    # 최신 글 먼저 (답글은 시간순 유지)
    return posts


@app.post("/feedback")
def api_feedback_add(req: FeedbackReq):
    import psycopg
    from config.settings import PG_DSN
    content = req.content.strip()
    if not content:
        return {"error": "내용을 입력하세요"}
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO feedback(author, content, parent_id) VALUES (%s,%s,%s) RETURNING fb_id",
                    (req.author.strip() or "익명", content[:2000], req.parent_id))
        return {"fb_id": cur.fetchone()[0]}


@app.get("/ocr.html")                 # 문서 접수(OCR) 페이지 — 스캔 txt 업로드·파싱 검수·정규화
def api_ocr_page():
    return FileResponse(_WEB / "ocr.html")


# ── 피드백 게시판 ──
@app.get("/feedback.html")            # 화면설계 피드백 페이지 (메인 디자인, /board API 사용)
def api_feedback_page():
    return FileResponse(_WEB / "feedback.html")


@app.get("/board.html")               # 게시판 페이지 서빙 (수행사 전달용 목업 v0.1)
def api_board_page():
    return FileResponse(_WEB / "board.html")


@app.get("/board")                    # 의견 목록 + 확인필요사항(Q&A) + 답변
def api_board_list(kind: str | None = None):
    import psycopg
    from psycopg.rows import dict_row
    from config.settings import PG_DSN
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        q = ("SELECT fb_id, parent_id, kind, org, dept, bunkwa, writer, menu, screen, area,"
             " vtype, importance, status, status_note, proposal, likes, content,"
             " qa_context, answer_pos, target,"
             " to_char(created_at,'MM-DD') AS created_at FROM feedback")
        params = ()
        if kind:
            q += " WHERE kind=%s"; params = (kind,)
        q += " ORDER BY fb_id DESC"
        cur.execute(q, params)
        return cur.fetchall()


class BoardReq(BaseModel):
    org: str; writer: str
    dept: str | None = None
    bunkwa: str | None = None
    menu: str | None = None
    screen: str | None = None
    area: str | None = None
    vtype: str | None = None
    importance: str | None = "m"
    content: str = ""
    proposal: dict | None = None


@app.post("/board")                   # 의견 등록
def api_board_add(req: BoardReq):
    import psycopg
    from config.settings import PG_DSN
    if not req.org.strip() or not req.writer.strip():
        return {"error": "작성자 정보(소속·성명)는 필수입니다."}
    if not req.content.strip() and not req.proposal:
        return {"error": "의견 내용을 입력하세요."}
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO feedback(kind, org, dept, bunkwa, writer, menu, screen, area,
                        vtype, importance, content, proposal, status)
                       VALUES ('opinion',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'접수') RETURNING fb_id""",
                    (req.org, req.dept, req.bunkwa, req.writer, req.menu, req.screen, req.area,
                     req.vtype, req.importance, req.content[:4000],
                     _json.dumps(req.proposal, ensure_ascii=False) if req.proposal else None))
        return {"fb_id": cur.fetchone()[0]}


@app.post("/board/{fb_id}/like")      # 공감(+1)
def api_board_like(fb_id: int):
    import psycopg
    from config.settings import PG_DSN
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("UPDATE feedback SET likes=COALESCE(likes,0)+1 WHERE fb_id=%s RETURNING likes", (fb_id,))
        row = cur.fetchone()
        return {"likes": row[0] if row else 0}


class AnswerReq(BaseModel):
    org: str; writer: str
    dept: str | None = None
    bunkwa: str | None = None
    answer_pos: str
    content: str = ""


@app.post("/board/{qa_id}/answer")    # 확인필요사항(Q&A) 답변 등록
def api_board_answer(qa_id: int, req: AnswerReq):
    import psycopg
    from config.settings import PG_DSN
    if not req.org.strip() or not req.writer.strip():
        return {"error": "작성자 정보(소속·성명)는 필수입니다."}
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO feedback(kind, parent_id, org, dept, bunkwa, writer, answer_pos, content)
                       VALUES ('answer',%s,%s,%s,%s,%s,%s,%s) RETURNING fb_id""",
                    (qa_id, req.org, req.dept, req.bunkwa, req.writer, req.answer_pos, req.content[:2000]))
        return {"fb_id": cur.fetchone()[0]}


@app.post("/similar-cases")           # 기능③
def api_similar(req: SimilarReq):
    vec = _emb.encode([req.summary])[0]
    return similar_case.find_similar(vec, req.review_type, req.kcd_codes, req.n)


@app.get("/review-doc/{case_id}")     # 안건 기반 의결서 생성 (화면 슬라이드5)
def api_review_doc_case(case_id: int, rule_no: str | None = None):
    """rule_no 미지정: 전 규칙 세트 반환(담당자 선택용). 지정: 채택 세트로 이유 재생성."""
    cases = {c["case_id"]: c for c in api_cases()}
    if case_id not in cases:
        return {"error": "안건 없음"}
    c = cases[case_id]
    target = "2개 이상" if len(c["kcd_codes"] or []) >= 2 else ""  # TODO(확인): 판단대상 산정 규칙
    doc = review_doc.draft(c["review_type"], c["review_content"], target,
                           c["facts"], _llm, _emb, c["kcd_codes"], rule_no=rule_no)
    doc["case"] = c
    doc["adopted_rule"] = rule_no
    return doc


@app.post("/review-doc")              # 기능⑤ (직접 입력형)
def api_review_doc(req: ReviewDocReq):
    return review_doc.draft(req.review_type, req.review_content, req.target_cond, req.facts, _llm, _emb, req.kcd_codes)


@app.post("/stats")                   # 기능⑥
def api_stats(q: Question):
    return stats.ask(q.question, _llm)
