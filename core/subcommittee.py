"""분과 프로필: 안건 -> 담당 분과 판정 + 체크리스트·심사기준 근거 제공.

TODO(확인): KCD 접두 기반 분과 배정은 매뉴얼 '분과 소개'에서 유추한 근사 규칙.
  실제 배정(접수 시 분과 지정 여부, 경계 질환 처리)은 보훈심사위원회 확인 필요.
"""
import json
from pathlib import Path
from functools import lru_cache

PROFILE_PATH = Path(__file__).parent.parent / "data" / "manuals" / "subcommittee_profiles.json"
MANUAL_DOCTYPE = {"1": "매뉴얼:1권", "2": "매뉴얼:2권", "5": "매뉴얼:2권",
                  "3": "매뉴얼:3권", "4": "매뉴얼:3권", "6": "매뉴얼:2권"}  # 6=족부·건(정형외과 계열, 2권 준용)


@lru_cache
def profiles() -> dict:
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def resolve(kcd_codes: list[str] | None, review_content: str = "") -> tuple[str, dict]:
    """(분과번호, 프로필). 우선순위: 심의내용 키워드(고엽제->4, 자해->5, 전몰/전상->1) > KCD 접두."""
    rc = review_content or ""
    if "고엽제" in rc or "사망" in rc and "자해" not in rc and any(
            (c or "")[:1] in "CEFIJK" for c in (kcd_codes or [])):
        return "4", profiles()["4"]
    if "자해" in rc:
        return "5", profiles()["5"]
    if any(k in rc for k in ("전몰", "전상", "독립")):
        return "1", profiles()["1"]
    for code in (kcd_codes or []):
        code = (code or "").upper()
        for sub in ("3", "2", "4", "5"):   # 접두 특이도 순 (M5/S0 척추·두부 우선)
            if any(code.startswith(p) for p in profiles()[sub]["kcd_prefix"]):
                return sub, profiles()[sub]
    return "1", profiles()["1"]            # 기본: 법률적 사실관계 분과
