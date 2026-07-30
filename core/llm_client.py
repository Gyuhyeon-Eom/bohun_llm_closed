"""FabriX 생성 LLM 게이트웨이. 모든 생성 LLM 호출은 이 클래스를 거친다.

TODO(확인): FabriX API 규격 미확정. FabrixClient._call()은 "OpenAI 호환
  chat/completions"라는 가정으로 임의 작성 - 삼성 회신 후 이 메서드와
  config/settings.py의 FABRIX_* 값만 수정하면 전체 기능에 반영된다.
"""
import time
from pathlib import Path
from config.settings import (FABRIX_ENDPOINT, FABRIX_API_KEY, FABRIX_MODEL,
                             LLM_TIMEOUT_S, LLM_MAX_RETRIES, LLM_TEMPERATURE,
                             LLM_PROVIDER_LABEL)

PROMPT_DIR = Path(__file__).parent / "prompts"


class LLMUnavailable(RuntimeError):
    """생성 LLM에 도달할 수 없는 상태 - 사용자에게 보여줄 원인·해결 문구를 담는다."""


class LLMTransient(RuntimeError):
    """재시도로 회복 가능성이 있는 일시적 오류(5xx·일시 연결 끊김 등).
    4xx·응답 스키마 불일치처럼 재시도해도 결과가 같은 오류와 구분한다."""


class LLMClient:
    """공통 골격: 프롬프트 렌더링 -> 재시도 -> 토큰 사용량 로깅."""

    def generate(self, prompt_name: str, **vars) -> str:
        template = (PROMPT_DIR / f"{prompt_name}.txt").read_text(encoding="utf-8")
        try:
            prompt = template.format(**vars)
        except (KeyError, IndexError) as e:
            # 프롬프트 템플릿의 플레이스홀더와 넘긴 변수가 어긋남(개발 실수). 재시도 무의미 -
            # 원인을 명확히 드러내 조용한 오작동을 막는다.
            raise RuntimeError(
                f"프롬프트 렌더링 실패({prompt_name}.txt): 누락/불일치 플레이스홀더 {e}") from e
        prompt = _scrub_rrn(prompt)   # 최종 방어선: 외부 API 전송 직전 주민번호 스크럽
        last_err = None
        for attempt in range(LLM_MAX_RETRIES + 1):
            try:
                return self._call(prompt, prompt_name)
            except LLMUnavailable:          # 연결 불가·모델 미설치 - 재시도 무의미, 즉시 전달
                raise
            except LLMTransient as e:       # 5xx·타임아웃 등 - 재시도 가치 있음
                last_err = e
                if attempt < LLM_MAX_RETRIES:
                    time.sleep(2 ** attempt)
            # 그 외 예외(4xx·스키마 불일치 등)는 재시도해도 동일하므로 그대로 전파
        raise RuntimeError(f"LLM 호출 실패({prompt_name}, {LLM_MAX_RETRIES + 1}회 시도): {last_err}")

    def _call(self, prompt: str, prompt_name: str = "") -> str:
        raise NotImplementedError


class FabrixClient(LLMClient):
    """OpenAI 호환 Chat API 게이트웨이 — 플랫폼 무관(FabriX/vLLM/Ollama 공통 경로).

    모델 라우팅(260730): 프롬프트명이 LLM_LIGHT_PROMPTS면 경량 모델(LLM_MODEL_LIGHT,
    예 gemma-4-31b), 아니면 심층 모델(LLM_MODEL_MAIN, 예 gpt-oss-120b) — 기능서 v0.1 배정표.
    보안(260730): SECURITY_FILTER_API 설정 시 입력/출력을 필터에 통과시킨다.
    """

    @staticmethod
    def _model_for(prompt_name: str) -> str:
        from config.settings import LLM_LIGHT_PROMPTS, LLM_MODEL_LIGHT, LLM_MODEL_MAIN
        return LLM_MODEL_LIGHT if prompt_name in LLM_LIGHT_PROMPTS else LLM_MODEL_MAIN

    def _call(self, prompt: str, prompt_name: str = "") -> str:
        import requests
        prompt = _security_gate(prompt, "in")
        model = self._model_for(prompt_name)
        # TODO(확인): OpenAI 호환 가정. 실규격에 맞춰 URL·헤더·페이로드 수정
        try:
            resp = requests.post(
                FABRIX_ENDPOINT,
                headers={"Authorization": f"Bearer {FABRIX_API_KEY}"},
                json={"model": model, "temperature": LLM_TEMPERATURE,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=LLM_TIMEOUT_S)
        except requests.exceptions.ConnectionError as e:
            raise LLMUnavailable(
                f"{LLM_PROVIDER_LABEL} 서버({FABRIX_ENDPOINT})에 연결할 수 없습니다 - "
                f"서비스 기동 여부와 엔드포인트(FABRIX_ENDPOINT)를 확인하세요.") from e
        except requests.exceptions.Timeout as e:
            # 타임아웃은 일시적일 수 있어 재시도 대상. 최종 실패 시 generate가 원인을 포함해 전달.
            raise LLMTransient(
                f"{LLM_PROVIDER_LABEL} 응답 시간 초과({LLM_TIMEOUT_S}초) - 모델 로딩·생성 지연. "
                f"지속되면 LLM_TIMEOUT_S를 늘리세요.") from e
        if resp.status_code == 404:
            raise LLMUnavailable(
                f"모델 '{model}'을 찾을 수 없습니다 - 모델명(LLM_MODEL_MAIN/LIGHT)과 배포 상태를 확인하세요.")
        if 400 <= resp.status_code < 500:   # 인증·잘못된 요청 - 재시도 무의미
            raise RuntimeError(f"{LLM_PROVIDER_LABEL} 요청 오류({resp.status_code}): {resp.text[:200]}")
        if resp.status_code >= 500:         # 서버측 일시 오류 - 재시도 가치 있음
            raise LLMTransient(f"{LLM_PROVIDER_LABEL} 서버 오류({resp.status_code})")
        data = resp.json()
        usage = data.get("usage", {})
        # 토큰 과금 추적 - TODO(확인): 운영에서는 로그 테이블/APM으로 전환
        print(f"[llm] model={model} tokens in={usage.get('prompt_tokens')} out={usage.get('completion_tokens')}")
        try:
            return _security_gate(data["choices"][0]["message"]["content"], "out")
        except (KeyError, IndexError, TypeError) as e:
            # 응답 스키마가 가정(OpenAI 호환)과 다름 - 재시도해도 동일. FabriX 실규격 회신 시 여기 조정.
            raise RuntimeError(
                f"{LLM_PROVIDER_LABEL} 응답 형식이 예상과 다릅니다(choices/message/content 없음): "
                f"{str(data)[:200]}") from e


def get_llm() -> "LLMClient":
    """설정(LLM_BACKEND) 기반 클라이언트 선택. openai=Ollama 로컬/FabriX 공통 경로."""
    from config.settings import LLM_BACKEND
    return FabrixClient() if LLM_BACKEND == "openai" else MockLLM()


class MockLLM(LLMClient):
    """개발·테스트용. canned에 prompt_name별 응답을 주입할 수 있다."""

    def __init__(self, canned: dict[str, str] | None = None):
        self.canned = canned or {}
        self._last_name = None

    def generate(self, prompt_name: str, **vars) -> str:
        self._last_name = prompt_name
        return super().generate(prompt_name, **vars)

    def _call(self, prompt: str, prompt_name: str = "") -> str:
        return self.canned.get(self._last_name, f"[MOCK] {self._last_name} 응답 ({len(prompt)}자 수신)")


class RuleCorrectLLM(LLMClient):
    """프로토타입용 OCR 교정기 - verifier의 FabriX 자리에 꽂는 규칙 기반 대역.
    운영에서는 FabrixClient가 이 역할을 하며, 여기선 "안전한" 규칙만 적용한다:
    노이즈 제거·줄바꿈 병합은 무조건, 문자 역치환은 정상 단어를 훼손할 수 없는
    경우(정상 표기에 거의 안 쓰이는 글자, 숫자 문맥의 O/l/B/S/ㅡ)만.
    실측 교훈: 무차별 역치환('재'->'제' 등)은 멀쩡한 '재신청'까지 훼손해
    적중률을 깎는다 - 문맥 교정(LLM)이 필요한 이유. FabriX 연결 후 비교 베이스라인으로 유지."""
    _SAFE_REV = {"멍": "명", "힝": "항", "싱": "심", "둥": "등", "흔": "훈"}  # 정상 표기 희귀 글자만
    _DIGIT_REV = {"O": "0", "l": "1", "B": "8", "S": "5", "ㅡ": "-"}
    _NOISE = set("·¸'˙‚ˈ|")

    def _call(self, prompt: str, prompt_name: str = "") -> str:
        text = prompt.split("[OCR 텍스트]")[-1].strip()
        text = "".join(c for c in text if c not in self._NOISE).replace("\n", " ")
        chars = list(text)
        for i, c in enumerate(chars):
            if c in self._SAFE_REV:
                chars[i] = self._SAFE_REV[c]
            elif c in self._DIGIT_REV:
                prev = chars[i-1] if i else ""
                nxt = chars[i+1] if i+1 < len(chars) else ""
                if prev.isdigit() or nxt.isdigit():   # 숫자 문맥에서만 (조문번호 복원)
                    chars[i] = self._DIGIT_REV[c]
        return " ".join("".join(chars).split())


# ── 보안 계층 (260730) ────────────────────────────────────────────────────
import re as _re

_RRN = _re.compile(r"(\d{6})[-\s]?([1-4])\d{6}")


def _scrub_rrn(text: str) -> str:
    """주민번호 스크럽 — 저장 계층 마스킹이 뚫렸을 때의 최종 방어선.
    외부 API로 나가는 모든 프롬프트에 적용(생년월일 6자리+성별코드는 보존)."""
    return _RRN.sub(r"\1-\2******", text)


def _security_gate(text: str, direction: str) -> str:
    """Security Filter API 훅. 미설정=통과. 차단 판정 시 예외(사유 포함).
    필터 장애: 기본 fail-open(스크럽은 이미 적용됨) + 경고 — SECURITY_FILTER_STRICT=1이면 차단."""
    from config.settings import SECURITY_FILTER_API, SECURITY_FILTER_MODE, SECURITY_FILTER_STRICT
    if not SECURITY_FILTER_API or direction not in SECURITY_FILTER_MODE:
        return text
    try:
        from core.provider_apis import security_check
        allowed, filtered, reason = security_check(text, direction)
        if not allowed:
            raise LLMUnavailable(f"보안 필터 차단({direction}): {reason or '정책 위반'}")
        return filtered
    except LLMUnavailable:
        raise
    except Exception as e:
        if SECURITY_FILTER_STRICT:
            raise LLMUnavailable(f"보안 필터 점검 불가(STRICT): {e}") from e
        import sys
        print(f"[security] 필터 장애 — 통과 처리(스크럽 적용됨): {e}", file=sys.stderr)
        return text
