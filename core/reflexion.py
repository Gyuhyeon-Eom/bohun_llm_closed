"""문서 생성 리플렉시온 루프 (v0.2): 초안 <-> 근거 대조 검증 -> 지적 반영 수정.

설계 원칙 유지: 사실 조립은 여전히 결정적(팩트시트·사건 자료)이며, 이 루프는
LLM 서술부의 환각·누락·결론 불일치를 근거와 대조해 걸러내는 검증 단계만 더한다.
검증 지적사항은 meta로 반환해 HITL 화면에서 담당자가 무엇이 걸러졌는지 확인한다.
"""
from config.settings import REFLEXION_MAX_PASSES
from core.llm_client import LLMClient

# 검증 LLM이 "이상 없음"을 표현하는 변형들 - 공백 제거 후 접두 일치로 판정
_OK_TOKENS = ("문제없음", "이상없음", "지적사항없음")


def _parse_issues(critique: str) -> list[str]:
    """critique 프롬프트 출력 -> 지적사항 목록. '문제없음' 계열이면 빈 목록."""
    text = (critique or "").strip()
    if not text:
        return []
    compact = text.replace(" ", "").replace(".", "")
    if any(compact.startswith(t) for t in _OK_TOKENS):
        return []
    issues = []
    for ln in text.splitlines():
        t = ln.strip().lstrip("-•*").strip()
        if not t or t.replace(" ", "") in _OK_TOKENS:
            continue
        issues.append(t)
    return issues[:5]   # critique 프롬프트와 동일 상한 - 폭주 방지


def refine(draft: str, evidence: str, verdict: str, llm: LLMClient,
           max_passes: int | None = None, postprocess=None) -> tuple[str, dict]:
    """초안을 검증(critique)-수정(revise) 루프에 통과시킨다.

    evidence: 대조할 결정적 근거(사건 자료·팩트시트 등 렌더링된 텍스트)
    verdict:  지정된 결론(담당자 선택 판단 축) - 결론 불일치 검증용
    postprocess: 수정본에 적용할 후처리 (예: _strip_markdown) - 원 생성 경로와 동일하게
    반환: (최종문안, meta). meta = {"critiques": 검증 횟수, "issues": 지적사항, "revised": bool}
    검증 LLM 호출이 실패하면 초안을 그대로 반환한다 - 리플렉시온은 부가 검증이지
    문서 생성 자체를 막는 관문이 아니다.
    """
    passes = REFLEXION_MAX_PASSES if max_passes is None else max_passes
    meta = {"critiques": 0, "issues": [], "revised": False}
    text = draft
    for _ in range(max(0, passes)):
        try:
            issues = _parse_issues(llm.generate(
                "critique", verdict=verdict, evidence=evidence, draft=text))
        except Exception:
            break
        meta["critiques"] += 1
        if not issues:
            break
        meta["issues"].extend(issues)
        try:
            revised = llm.generate(
                "revise", evidence=evidence, draft=text,
                issues="\n".join(f"- {i}" for i in issues)).strip()
        except Exception:
            break
        if not revised:      # 빈 수정본이면 초안 유지 (수정 실패가 문서를 비우면 안 됨)
            break
        text = postprocess(revised) if postprocess else revised
        meta["revised"] = True
    return text, meta
