"""분과 심사 매뉴얼(텍스트) -> ①분과 프로필(체크리스트·관할) ②적재용 블록 JSON.

사용: python3 data/manuals/extract_manuals.py <매뉴얼 디렉토리>
매뉴얼 원본은 '보훈심사실무 N분과' 직원용 문서 (프로젝트 제공분은 UTF-8 텍스트).
"""
import json, re, sys, glob
from pathlib import Path

OUT = Path(__file__).parent

PROFILES = {  # 관할은 각 매뉴얼 '분과 소개'에서 발췌. KCD 매핑은 근사 - TODO(확인): 실제 배정 규칙
    "1": {"name": "제1분과", "specialty": "독립유공·전몰/전상군경·보상심사(법률적 사실관계, 패스트트랙)",
          "kcd_prefix": [], "keywords": ["전몰", "전상", "독립유공", "보상", "패스트트랙"]},
    "2": {"name": "제2분과", "specialty": "정형외과 — 상지·하지·골반 근골격계",
          "kcd_prefix": ["M0", "M1", "M2", "M6", "M7", "M8", "M9", "S4", "S5", "S6", "S7", "S8", "S9"],
          "keywords": ["상지", "하지", "골반", "관절", "골절"]},
    "3": {"name": "제3분과", "specialty": "신경외과·신경과 — 척추·두부",
          "kcd_prefix": ["M4", "M5", "S0", "S1", "S2", "S3", "G"],
          "keywords": ["척추", "추간판", "두부", "뇌"]},
    "4": {"name": "제4분과", "specialty": "내과·정신질환·고엽제후유(의)증·상이사망",
          "kcd_prefix": ["C", "D", "E", "F", "I", "J", "K", "N0", "A", "B"],
          "keywords": ["내과", "종양", "당뇨", "심장", "정신", "고엽제", "상이사망"]},
    "5": {"name": "제5분과", "specialty": "이비인후과·안과·치과·비뇨기과·기타·자해사망",
          "kcd_prefix": ["H", "N2", "N3", "N4"],
          "keywords": ["난청", "이명", "고막", "안과", "치과", "비뇨", "자해"]},
}
MANUAL_OF = {"1": "1", "2": "2", "5": "2", "3": "3", "4": "3"}  # 분과 -> 매뉴얼 파일 번호


def parse_checklist(text: str) -> list[dict]:
    """'검토서 체크리스트' 본문 -> [{item, subs[]}]. 3개 분과 공통 코어."""
    idx = text.rfind("검토서 체크리스트")
    seg = text[idx:idx + 3500]
    end = re.search(r"패스트 트랙|분야별 심사|보훈심사 개요", seg[100:])
    seg = seg[:100 + end.start()] if end else seg
    items, cur = [], None
    for line in seg.splitlines():
        ln = line.strip()
        if not ln or ln in ("체 크 사 항", "이상여부(체크)", "○ ×", "O X", "검토서 체크리스트") \
           or re.fullmatch(r"[ⅠⅡⅢⅣⅤ\d\s]+", ln):
            continue
        if ln[0] in "-･∙․":
            if cur is not None:
                cur["subs"].append(ln.lstrip("-･∙․ "))
        else:
            cur = {"item": ln, "subs": []}
            items.append(cur)
    # 줄바꿈으로 쪼개진 항목 병합 휴리스틱: '~는지/여부/확인'으로 안 끝나면 다음 항목과 결합
    merged = []
    for it in items:
        if merged and not re.search(r"(는지\??|여부|확인|경우)$", merged[-1]["item"]) and not merged[-1]["subs"]:
            merged[-1]["item"] += " " + it["item"]; merged[-1]["subs"] = it["subs"]
        else:
            merged.append(it)
    return merged


def to_blocks(text: str, manual_no: str) -> list[dict]:
    """매뉴얼 전문 -> 문단 블록 (빈 줄 없는 연속 텍스트라 길이 기준 분절)."""
    paras = re.split(r"\n(?=[ⅠⅡⅢⅣⅤⅥ]\s|\d+\.\s|[가-힣]+\s심사기준)", text)
    blocks = []
    for p in paras:
        p = " ".join(p.split())
        if len(p) < 40 or p.count("·") > 30:  # 목차 점선 등 제외
            continue
        blocks.append({"type": "paragraph", "text": p[:2400], "page": 1, "confidence": 1.0})
    return blocks


def main(src: str):
    files = {re.search(r"(\d)__", f).group(1): f for f in glob.glob(f"{src}/*심사*분과*.pdf")}
    texts = {no: open(f, encoding="utf-8", errors="replace").read() for no, f in files.items()}
    for sub, prof in PROFILES.items():
        prof["checklist"] = parse_checklist(texts[MANUAL_OF[sub]])
        prof["manual"] = f"보훈심사실무 {MANUAL_OF[sub]}권"
    (OUT / "subcommittee_profiles.json").write_text(
        json.dumps(PROFILES, ensure_ascii=False, indent=1))
    print("프로필 5개 (체크리스트", len(PROFILES["1"]["checklist"]), "항목)")
    for no, t in texts.items():
        blocks = to_blocks(t, no)
        (OUT / f"manual_{no}.json").write_text(
            json.dumps({"doc_id": f"manual_{no}", "blocks": blocks}, ensure_ascii=False))
        print(f"매뉴얼 {no}권: 블록 {len(blocks)}개")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
