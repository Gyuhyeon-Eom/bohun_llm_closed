"""법령 약칭·조문 축약 표기의 결정적 확장.

'보상법 2-1-3' -> '보상법 제2조제1항제3호 보훈보상대상자 지원에 관한 법률' 처럼
검색 재현율을 높이는 확장은 LLM이 아니라 여기서 결정적으로 처리한다.
근거: 48문항 실측(scripts/eval_result.json)에서 7B LLM이 보상법/예우법의
정식 명칭을 혼동해 재작성하는 사례 확인 - 법령명 매핑은 환각이 허용되지 않는다.
"""
import re

LAW_FULLNAME = {"예우법": "국가유공자 등 예우 및 지원에 관한 법률",
                "보상법": "보훈보상대상자 지원에 관한 법률",
                "보상자법": "보훈보상대상자 지원에 관한 법률",
                "고엽제법": "고엽제후유의증 등 환자지원 및 단체설립에 관한 법률"}

# '<법명> 조-항(-호)' 축약: '예우법 4-1-6'(조항호), '고엽제법 28-1'(조항)
_CLAUSE = re.compile(r"(\S+법)\s*(\d+)-(\d+)(?:-(\d+))?")


def has_clause(text: str) -> bool:
    """문장에 '<법명> 조-항(-호)' 축약 표기가 있는지 - 법령 문서군 우선 검색 트리거."""
    return bool(_CLAUSE.search(text))


def clause_notes(text: str) -> list[str]:
    """문장 속 조문 축약별 '축약 = 원문 표기' 안내 (챗봇 컨텍스트 주입용).
    실측: 7B LLM이 '4-1-6'과 '제4조제1항제6호'를 같은 조문으로 연결하지 못해
    법령 원문이 컨텍스트에 있어도 '확인되지 않음'으로 답하는 문제 보완."""
    notes = []
    for m in _CLAUSE.finditer(text):
        name, jo, hang, ho = m.groups()
        ref = f"제{jo}조제{hang}항" + (f"제{ho}호" if ho else "")
        full = LAW_FULLNAME.get(name)
        notes.append(f"'{m.group(0)}' = {name} {ref}" + (f" ({full})" if full else ""))
    return notes


def expand_clauses(text: str) -> str:
    """문장 속 조문 축약을 '약칭 + 법령 원문 표기 + 정식 명칭'으로 확장.
    약칭은 남긴다(sparse 어휘 일치용). 축약 표기가 없으면 원문 그대로 반환."""
    def _one(m):
        name, jo, hang, ho = m.groups()
        ref = f"제{jo}조제{hang}항" + (f"제{ho}호" if ho else "")
        return " ".join(x for x in (name, ref, LAW_FULLNAME.get(name, "")) if x)
    return _CLAUSE.sub(_one, text)
