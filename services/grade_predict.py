"""상이등급 AI 판정예측 v2 (T/F 제공 자료 기반).

1차 근거: 시행령 [별표3] 상이등급구분표 189개 기준(grade_criteria) - 상병명·소견을
  임베딩으로 대조해 부합 기준을 찾고, 그 기준의 등급·분류번호·원문을 근거로 제시.
2차 참고: 과거 등급 판정 사례 풀(grade_case) 유사 조회.

사유문은 별표3 기준 원문을 인용해 결정적으로 조립한다(생성 LLM 불필요).
결과는 참고용이며 판정은 신체검사·보훈심사위원회 의결로 확정된다.
적재: python3 scripts/ingest_grade_criteria.py (별표3), mockgen(사례 풀)
"""
from collections import defaultdict

import psycopg
from psycopg.rows import dict_row

from config.settings import PG_DSN

RATIONALE_T = ("인정 상이처 '{disease}'에 대하여 제출된 진단서, 해당 진료과목 전문의의 신체검사"
               " 소견을 종합하여 살펴보건대, 이는 「국가유공자 등 예우 및 지원에 관한 법률 시행령」"
               " [별표3] 상이등급구분표 {class_no}호 \"{criterion}\"에 부합하는 것으로 판단되어"
               " 상이등급 '{grade} {class_no}호'에 해당함.")

NOTE = ("본 예측은 시행령 별표3 기준·과거 판정 사례 대조에 따른 참고 정보이며, "
        "상이등급은 신체검사와 보훈심사위원회 의결로 확정됩니다.")


def _criteria(cur, query, vec, body_part, k):
    """별표3 기준 검색: 어휘 부분일치(조사 변이 무관) + 임베딩 코사인 결합.
    기준문이 부위당 최대 43개의 정형문이라 전수 스코어링이 가장 투명·정확하다.
    - 어휘: 질의 토큰(2자 이상)이 기준문에 부분문자열로 나타나면 토큰 길이만큼 가점
      (예: '경도' ⊂ '경도의', '기능장애' ⊂ '기능장애가')
    - 임베딩: 동점 구간의 의미적 순위 (bge-m3 등 실제 임베더에서 효과)
    - 부위 지정 시 해당 절 내 탐색 (별표3의 부위별 구성과 일치)"""
    import re
    part_where = "WHERE body_part = %(p)s" if body_part else ""
    cur.execute(f"""
        SELECT class_no, grade, section, body_part, description,
               (1 - (embedding <=> %(v)s::vector)) AS cos
        FROM grade_criteria {part_where}""", {"v": vec, "p": body_part})
    rows = cur.fetchall()
    tokens = [t for t in re.findall(r"[가-힣A-Za-z0-9]{2,}", query)]
    scored = []
    for r in rows:
        desc = r["description"]
        lex = sum(len(t) for t in set(tokens) if t in desc)
        r["similarity"] = round(lex + float(r["cos"]), 4)
        r.pop("cos")
        scored.append(r)
    scored.sort(key=lambda r: (-r["similarity"], r["class_no"]))
    return scored[:k]


def _cases(cur, vec, body_part, k):
    cur.execute("""
        SELECT recv_no, meeting_date::text AS meeting_date, disease_name, body_part, grade,
               order_text, opinion_text,
               round((1 - (name_embedding <=> %(v)s::vector))::numeric, 4) AS similarity
        FROM grade_case
        ORDER BY (body_part = %(p)s) DESC, name_embedding <=> %(v)s::vector
        LIMIT %(k)s""", {"v": vec, "p": body_part or "", "k": k})
    return cur.fetchall()


def predict(disease_name: str, body_part: str | None, emb, n: int = 5) -> dict:
    vec = emb.encode([f"{body_part or ''} {disease_name}".strip()])[0]
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn, conn.cursor() as cur:
        crits = _criteria(cur, disease_name, vec, body_part, 6)
        cases = _cases(cur, vec, body_part, n)

    if not crits:
        if not cases:
            return {"grade1": None, "grade2": None, "rationale": None, "criteria": [],
                    "similar": [], "note": "별표3 기준 미적재 - scripts/ingest_grade_criteria.py 실행 후 사용하세요."}
        score = defaultdict(float)
        for c in cases:
            score[c["grade"]] += float(c["similarity"])
        ranked = sorted(score.items(), key=lambda kv: kv[1], reverse=True)
        return {"grade1": ranked[0][0], "grade2": ranked[1][0] if len(ranked) > 1 else None,
                "rationale": None, "criteria": [], "similar": cases,
                "note": NOTE + " (별표3 기준 미적재 상태 - 사례 기반 참고치)"}

    top = crits[0]
    second = next((c for c in crits[1:]
                   if (c["grade"], c["class_no"]) != (top["grade"], top["class_no"])), None)
    grade1 = f"{top['grade']} {top['class_no']}호"
    grade2 = f"{second['grade']} {second['class_no']}호" if second else None
    rationale = RATIONALE_T.format(disease=disease_name, class_no=top["class_no"],
                                   criterion=top["description"], grade=top["grade"])
    return {"grade1": grade1, "grade2": grade2, "rationale": rationale,
            "criteria": crits[:4], "similar": cases, "note": NOTE}
