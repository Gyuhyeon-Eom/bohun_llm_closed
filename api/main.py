"""FastAPI 진입점 - 기능②③⑤⑥. 화면/통합보훈시스템 연계는 이 API를 호출.

기동: uvicorn api.main:app --host 0.0.0.0 --port 8000
TODO(확인): 운영 전환 시 MockLLM -> FabrixClient, HashEmbedder -> bge로 교체
  (환경변수 EMBED_BACKEND=bge + 아래 _llm 한 줄)
"""
import json as _json
import os
import tempfile, threading, time
from pathlib import Path
from fastapi import FastAPI, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from ingestion.types import Block, BlockType
from ingestion.verifier import verify_blocks
from ingestion.chunker import chunk_blocks
from ingestion.indexer import index_document
from core.llm_client import RuleCorrectLLM
from pydantic import BaseModel
from core.llm_client import get_llm
from ingestion.embedder import get_embedder
from services import chatbot, similar_case, review_doc, stats, decision_doc, grade_predict

app = FastAPI(title="보훈심사 AI 지원", version="0.2")
_WEB = Path(__file__).parent.parent / "web"
app.mount("/img", StaticFiles(directory=_WEB / "img"), name="img")
app.mount("/css", StaticFiles(directory=_WEB / "css"), name="css")
app.mount("/js", StaticFiles(directory=_WEB / "js"), name="js")
_llm = get_llm()          # LLM_BACKEND=openai면 Ollama/FabriX, 기본은 mock
_emb = get_embedder()


class Question(BaseModel):
    question: str
    only_uploaded: bool = False   # True면 UI로 넣은 문서만 검색
    history: list[dict] = []      # [{"role":"user"|"ai","text":...}] 최근 대화 (챗봇용)
    session_id: int | None = None # 챗봇 세션 — None이면 첫 질문 시 새 세션 자동 생성
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


@app.post("/chatbot")                 # 기능② (성공 왕복은 세션 기록으로 저장)
def api_chatbot(q: Question):
    global _active_chats
    from core.llm_client import LLMUnavailable
    with _load_lock:
        _active_chats += 1
    try:
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


@app.get("/cases")                    # 안건 목록 (사건 스키마: application)
def api_cases():
    import psycopg
    from psycopg.rows import dict_row
    from config.settings import PG_DSN
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("""SELECT a.app_id, a.recv_no, a.applicant, a.duty_type, a.is_death,
                              a.review_content, a.subcommittee, a.round, a.status, a.apply_kind, a.track,
                              a.is_real,
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
    return rows


@app.post("/cases/demo-seed")         # 정형화틀 기반 목데이터 6건 생성
def api_demo_seed():
    import mockgen.generate_cases as g
    g.main()
    import db.build_graph as bg
    bg.main()                          # 유사사례 그래프 재생성
    return api_cases()


class JudgeReq(BaseModel):
    dis_id: int
    yeu_result: str                    # '해당' | '비해당'
    bosang_result: str


class FinalizeReq(BaseModel):
    dis_id: int
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


@app.get("/decision-doc/{app_id}/export")   # 심의의결서 산출물 (fmt=txt|pdf, dis_id=상이처 개별본)
def api_decision_export(app_id: int, fmt: str = "txt", dis_id: int | None = None):
    if fmt == "pdf":
        try:
            fname, path = decision_doc.export_pdf(app_id, _emb, dis_id)
        except ModuleNotFoundError:
            return {"error": "PDF 생성 모듈(reportlab) 미설치 — pip install reportlab 후 서버 재시작"}
        media = "application/pdf"
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


@app.post("/decision-doc/{app_id}/judge")     # 이원 판단 선택 -> LLM 판단내용 생성·저장
def api_judge(app_id: int, req: JudgeReq):
    return decision_doc.draft_judgment(app_id, req.dis_id, req.yeu_result, req.bosang_result, _llm, _emb)


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
    disease_name: str
    body_part: str | None = None
    n: int = 5
    ga_id: int | None = None   # 담당자 유사사례 선별 반영용


class CaseFileReq(BaseModel):
    kind: str = "추가 자료"
    title: str
    dis_id: int | None = None
    note: str | None = None


@app.get("/cases/{app_id}/files")             # 사건 자료함 (자동 파생 + 추가분, 최종 자료 우선)
def api_case_files(app_id: int):
    from services import case_file
    return case_file.list_files(app_id)


@app.post("/cases/{app_id}/files")            # 자료 메타 추가 (행 단위 — JSON append 불필요)
def api_case_file_add(app_id: int, req: CaseFileReq):
    from services import case_file
    return case_file.add(app_id, req.kind, req.title, req.dis_id, req.note)


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
        fname = f"심의의결서_조립_{app_id}.pdf"
        path = os.path.join(tempfile.gettempdir(), fname)
        _text_to_pdf(text, path)
        return FileResponse(path, filename=fname, media_type="application/pdf")
    fname = f"심의의결서_조립_{app_id}.txt"
    path = os.path.join(tempfile.gettempdir(), fname)
    open(path, "w", encoding="utf-8").write(text)
    return FileResponse(path, filename=fname, media_type="text/plain; charset=utf-8")


class FieldEditReq(BaseModel):
    field: str
    value: str
    dis_id: int | None = None
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
    case_id: int
    kind: str                  # exclude | pin | clear
    app_id: int | None = None
    dis_id: int | None = None
    ga_id: int | None = None
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


@app.get("/cases-search")             # 위원 직접 추가용 사례 검색 (요약문·KCD)
def api_cases_search(q: str, n: int = 10):
    from services import similar_pick
    return similar_pick.search_cases(q, n)


@app.get("/grade-agendas")            # 상이등급심사 안건 목록 (화면 v0.4)
def api_grade_agendas():
    import psycopg
    from psycopg.rows import dict_row
    from config.settings import PG_DSN
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM grade_agenda ORDER BY ga_id")
        return cur.fetchall()


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
    return grade_predict.predict(req.disease_name, req.body_part, _emb, req.n, req.ga_id)


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


@app.get("/grade-agendas/{ga_id}/export")  # 상이등급 심사표 xlsx 산출물 (확정 양식 14컬럼)
def api_grade_export(ga_id: int):
    from services import grade_export
    fname, path = grade_export.export_xlsx(ga_id, emb=_emb)
    return FileResponse(path, filename=fname,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


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
                    " app_id, created_at FROM scan_doc ORDER BY sd_id")
        return cur.fetchall()


@app.get("/scan-docs/{sd_id}")        # 스캔 문서 상세 (파싱된 검사 블록)
def api_scan_doc(sd_id: int):
    import psycopg
    from psycopg.rows import dict_row
    from config.settings import PG_DSN
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("SELECT sd_id, reg_no, person, sex_age, hospital, doc_kind, file_name,"
                    " pages, ocr_used, exams, app_id FROM scan_doc WHERE sd_id=%s", (sd_id,))
        row = cur.fetchone()
        return row or {"error": "스캔 문서 없음"}


@app.get("/scan-docs/{sd_id}/file")   # 스캔 원본 PDF (열람·다운로드)
def api_scan_doc_file(sd_id: int, dl: int = 0):
    import psycopg
    from config.settings import PG_DSN
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT orig_path, file_name FROM scan_doc WHERE sd_id=%s", (sd_id,))
        row = cur.fetchone()
    if not row or not row[0] or not os.path.exists(row[0]):
        return {"error": "원본 파일 없음"}
    disp = "attachment" if dl else "inline"
    return FileResponse(row[0], filename=row[1], media_type="application/pdf",
                        content_disposition_type=disp)


@app.post("/scan-docs/{sd_id}/to-case")   # 스캔 문서 → 요건심사 사건 변환 (HITL 전제)
def api_scan_to_case(sd_id: int):
    from services import scan_to_case
    return scan_to_case.to_case(sd_id)


@app.post("/scan-docs/{sd_id}/to-grade")  # 스캔 문서 → 상이등급 안건 변환 (신검 서류)
def api_scan_to_grade(sd_id: int):
    from services import scan_to_case
    return scan_to_case.to_grade(sd_id)


@app.post("/scan-docs/{sd_id}/normalize")  # OCR 텍스트 LLM 정규화 (260721 회의 반영)
def api_scan_normalize(sd_id: int, force: int = 0):
    from services import ocr_normalize
    return ocr_normalize.normalize_scan(sd_id, force=bool(force))


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
    parent_id: int | None = None


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
