"""기능⑤ 검토서 자동생성 v2 = 지식그래프(뼈대) + RAG(원문 근거) + LLM(서술만).

팩트시트 방식: 근거 체인(주문·조문·표준문안·유사사례)은 그래프 탐색으로
결정적으로 수집해 누락·환각을 차단하고, LLM은 완성된 팩트시트를 문장화만 한다.
산출물은 status=HITL_REVIEW - 담당자 검토·수정 전제.
"""
import re
import psycopg
from config.settings import PG_DSN
from core.llm_client import LLMClient
from core.graph import applied_clauses, cases_by_kcd
from core.subcommittee import resolve as resolve_sub, manual_doctype
from core.retrieval import hybrid_search

LAW_FULLNAME = {"예우법": "국가유공자 등 예우 및 지원에 관한 법률",
                "보상법": "보훈보상대상자 지원에 관한 법률",
                "보상자법": "보훈보상대상자 지원에 관한 법률",
                "고엽제법": "고엽제후유의증 등 환자지원 및 단체설립에 관한 법률"}


def _expand_clause(clause: str) -> str:
    """'예우법 4-1-6' -> '예우법 제4조제1항제6호 ...' (법령 원문 표기로 확장해 검색 재현율 확보)."""
    m = re.match(r"\s*(\S+?)\s*(\d+)-(\d+)-(\d+)\s*$", clause)
    if not m:
        return clause
    name, jo, hang, ho = m.groups()
    return f"{name} 제{jo}조제{hang}항제{ho}호 {LAW_FULLNAME.get(name, '')}".strip()


def build_fact_sheet(review_type: str, review_content: str, target_cond: str,
                     kcd_codes: list[str], facts: str, emb,
                     rule_no: str | None = None) -> dict:
    """검토서 근거 팩트시트. 각 슬롯은 결정적 소스에서 채운다.
    rule_no: 채택할 주문안 규칙 세트. 미지정 시 전 세트 수집(화면에서 담당자 선택).
    TODO(확인): 규칙 특정의 업무 기준(관리번호 선택 주체·조건) 확인 필요."""
    orders = applied_clauses(review_content, target_cond)          # 그래프: 주문·조문·표준문안
    if rule_no is not None:
        orders = [o for o in orders if str(o["rule_no"]) == str(rule_no)]
    similar = cases_by_kcd(kcd_codes)                              # 그래프: 동일 상이처 과거 판정
    passages = {}                                                  # RAG: 조문 원문 구절
    for clause in dict.fromkeys(o["clause"] for o in orders):      # 중복 제거, 순서 유지
        q = _expand_clause(clause)
        hits = hybrid_search(q, emb.encode([q])[0], top_k=2, doc_type="법령")  # 법령 우선
        if not hits:
            hits = hybrid_search(q, emb.encode([q])[0], top_k=2)               # 폴백: 전체
        passages[clause] = [{"content": h["content"][:500], "source": h["source_path"],
                             "page": h["page_no"]} for h in hits]
    kcd_names = _kcd_names(kcd_codes)
    # 분과별 가중: 안건 분과의 매뉴얼에서 질환 심사기준 발췌를 1순위 근거로 수집
    sub_no, sub = resolve_sub(kcd_codes, review_content)
    criteria = []
    disease_terms = " ".join(k["disease"] or k["code"] for k in kcd_names) or review_content
    q = f"{disease_terms} 판단기준 심사 포인트"
    for h in hybrid_search(q, emb.encode([q])[0], top_k=2, doc_type=manual_doctype(sub_no)):
        criteria.append({"content": h["content"][:600], "source": sub["manual"]})
    return {"review_type": review_type, "review_content": review_content,
            "target_cond": target_cond, "kcd": kcd_names, "facts": facts,
            "subcommittee": {"no": sub_no, "name": sub["name"], "specialty": sub["specialty"]},
            "checklist": sub["checklist"], "criteria_passages": criteria,
            "orders": orders, "clause_passages": passages, "similar_cases": similar}


def _kcd_names(codes: list[str]) -> list[dict]:
    if not codes:
        return []
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT ON (kcd_code) kcd_code, disease_name, kcd_name"
                    " FROM kcd WHERE kcd_code = ANY(%s)", (codes,))
        found = {c: {"code": c, "disease": d, "kcd_name": k} for c, d, k in cur.fetchall()}
    return [found.get(c, {"code": c, "disease": None, "kcd_name": None}) for c in codes]


def _render_fact_sheet(fs: dict) -> str:
    lines = [f"심의유형: {fs['review_type']} / 심의내용: {fs['review_content']}"
             f" / 판단대상: {fs['target_cond'] or '(단일)'}"]
    lines.append("상이처(KCD): " + (", ".join(
        f"{k['code']} {k['disease'] or ''}" for k in fs["kcd"]) or "없음"))
    lines.append("적용 조문(주문):")
    for o in fs["orders"]:
        lines.append(f"  - {o['clause']} -> {o['result']} (표준문안 {o['std_code'] or '없음'})")
    lines.append("조문 원문 근거:")
    for cl, ps in fs["clause_passages"].items():
        for p in ps:
            lines.append(f"  - [{cl}] {p['content'][:200]} ({p['source']} p.{p['page']})")
    lines.append(f"담당 분과: {fs['subcommittee']['name']} ({fs['subcommittee']['specialty']})")
    if fs["criteria_passages"]:
        lines.append("분과 심사기준 발췌 (분과 매뉴얼):")
        for cp in fs["criteria_passages"]:
            lines.append(f"  - {cp['content'][:250]} ({cp['source']})")
    lines.append("동일 상이처 과거 판정:")
    for s in fs["similar_cases"]:
        lines.append(f"  - 사례 {s['case_id']}: {s['decision']} (일치 KCD {s['shared_kcd']}개)")
    return "\n".join(lines)


def _strip_markdown(text: str) -> str:
    """LLM이 규칙을 어기고 마크다운을 낼 때의 안전망 - 공문서엔 서식 기호가 없어야 한다."""
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)     # 헤더
    text = re.sub(r"^\s*[-*]{3,}\s*$", "", text, flags=re.M)  # 구분선
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)           # 굵게
    text = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"\1", text)  # 기울임
    text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.M)    # 불릿
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def draft(review_type: str, review_content: str, target_cond: str, facts: str,
          llm: LLMClient, emb, kcd_codes: list[str] | None = None,
          rule_no: str | None = None) -> dict:
    fs = build_fact_sheet(review_type, review_content, target_cond, kcd_codes or [], facts, emb,
                          rule_no=rule_no)
    # v5 프롬프트 신규 변수: 판정 방향(그래프 주문 요약)·분과 심사기준 모듈
    from core.subcommittee_modules import module_for
    verdict_hint = " / ".join(f"{o['clause']} {o['result']}" for o in fs["orders"]) or "(주문 후보 없음 — 자료 기반 판단)"
    body = _strip_markdown(llm.generate("review_doc", review_type=review_type,
                        verdict_hint=verdict_hint,
                        module_criteria=module_for(fs["subcommittee"]["no"]),
                        fact_sheet=_render_fact_sheet(fs), facts=facts))
    order_text = "\n".join(f"{o['pair_order']}. {o['clause']} : {o['result']}" for o in fs["orders"])
    return {"fact_sheet": fs, "order_text": order_text,
            "body_draft": body, "status": "HITL_REVIEW"}
