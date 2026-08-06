# -*- coding: utf-8 -*-
"""OCR 정확도 정량 평가 — 사이냅 / VLM / tesseract 를 같은 자로 비교.

지표:
  CER        문자 오류율 = 편집거리/정답 글자수 (공백 정규화 후) — 낮을수록 좋음
  숫자CER    숫자·KCD코드·등록번호 토큰만의 오류율 — 환각 치명도 (이 시스템에선 CER보다 중요)
  필드 P/R   핵심 필드(날짜·KCD·등급·등록번호) 집합의 정밀도/재현율
  판독불가율 ⟦판독불가⟧ 표기 비율 — VLM 정직성(못 읽으면 지어내지 않고 표기하는지)

사용:
  1) 골드셋 평가(권장): 정답 전사 파일과 후보를 비교
     python3 scripts/eval_ocr_accuracy.py --ref gold/page01.txt --hyp synap/page01.txt vlm/page01.txt
  2) 디렉터리 일괄: 같은 파일명끼리 매칭
     python3 scripts/eval_ocr_accuracy.py --ref-dir gold/ --hyp-dir synap/ vlm/
  3) 정답 없이 상호 대조(참고용 — 정확도 아님):
     python3 scripts/eval_ocr_accuracy.py --pair synap_full.txt vlm_full.txt

골드셋 만드는 법: 대표 페이지 15~20장(문서유형×스캔품질별)을 사람이 정확히 타이핑.
공백·줄바꿈은 자동 정규화되므로 글자만 정확하면 된다.
"""
import argparse
import json
import re
import sys
from pathlib import Path

RE_NUMTOKEN = re.compile(r"[A-Z]?\d[\d.\-/]*")          # 숫자·KCD(M17.1)·등록번호·날짜 토큰
RE_KCD = re.compile(r"(?<![A-Za-z0-9])[A-Z]\d{2}(?:\.\d{1,2})?(?!\d)")
RE_DATE = re.compile(r"\d{4}[.\-/년\s]{1,2}\d{1,2}[.\-/월\s]{1,2}\d{1,2}")
RE_GRADE = re.compile(r"\d급\s?\d?\s?항?\s?\d{0,4}\s?호?")
RE_REGNO = re.compile(r"\d{8}|\d{2}-\d{6,}")
UNREADABLE = "⟦판독불가⟧"


def norm(s: str) -> str:
    s = s.replace(UNREADABLE, " ")
    return re.sub(r"\s+", " ", s).strip()


def edit_distance(a: str, b: str) -> int:
    """반복 O(len(a)*len(b)) 레벤슈타인 — 페이지 단위 텍스트에 충분."""
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(ref: str, hyp: str) -> float:
    r = norm(ref)
    return round(edit_distance(r, norm(hyp)) / max(len(r), 1), 4)


def num_cer(ref: str, hyp: str) -> float:
    r = " ".join(RE_NUMTOKEN.findall(ref))
    h = " ".join(RE_NUMTOKEN.findall(hyp))
    return round(edit_distance(r, h) / max(len(r), 1), 4)


def fields(s: str) -> set:
    out = set()
    for rx, tag in ((RE_KCD, "kcd"), (RE_DATE, "date"), (RE_GRADE, "grade"), (RE_REGNO, "regno")):
        for m in rx.findall(s):
            out.add((tag, re.sub(r"\s", "", m)))
    return out


def field_pr(ref: str, hyp: str):
    R, H = fields(ref), fields(hyp)
    if not R:
        return None, None
    tp = len(R & H)
    prec = round(tp / len(H), 3) if H else 0.0
    rec = round(tp / len(R), 3)
    return prec, rec


def evaluate(ref: str, hyp: str) -> dict:
    return {
        "CER": cer(ref, hyp),
        "숫자CER": num_cer(ref, hyp),
        "필드정밀도": field_pr(ref, hyp)[0],
        "필드재현율": field_pr(ref, hyp)[1],
        "판독불가율": round(hyp.count(UNREADABLE) / max(len(norm(hyp)), 1) * 100, 2),
        "정답글자수": len(norm(ref)), "후보글자수": len(norm(hyp)),
    }


def main():
    ap = argparse.ArgumentParser(description="OCR 정확도 정량 평가")
    ap.add_argument("--ref", help="정답(골드) 전사 파일")
    ap.add_argument("--hyp", nargs="+", help="후보 파일들 (사이냅/VLM/tesseract 산출)")
    ap.add_argument("--ref-dir", help="정답 디렉터리 (파일명 매칭 일괄)")
    ap.add_argument("--hyp-dir", nargs="+", help="후보 디렉터리들")
    ap.add_argument("--pair", nargs=2, help="정답 없이 두 산출물 상호 대조(참고용)")
    ap.add_argument("--json", action="store_true", help="JSON 출력")
    args = ap.parse_args()

    results = {}
    if args.pair:
        a, b = (Path(p).read_text(encoding="utf-8", errors="replace") for p in args.pair)
        d = edit_distance(norm(a), norm(b))
        results["상호대조(정확도 아님)"] = {
            "상호CER": round(d / max(len(norm(a)), 1), 4),
            "숫자토큰 일치율": round(len(set(RE_NUMTOKEN.findall(a)) & set(RE_NUMTOKEN.findall(b)))
                                / max(len(set(RE_NUMTOKEN.findall(a)) | set(RE_NUMTOKEN.findall(b))), 1), 3),
            "필드 교집합": len(fields(a) & fields(b)), "필드 합집합": len(fields(a) | fields(b)),
        }
    elif args.ref and args.hyp:
        ref = Path(args.ref).read_text(encoding="utf-8", errors="replace")
        for h in args.hyp:
            results[h] = evaluate(ref, Path(h).read_text(encoding="utf-8", errors="replace"))
    elif args.ref_dir and args.hyp_dir:
        for rf in sorted(Path(args.ref_dir).glob("*.txt")):
            ref = rf.read_text(encoding="utf-8", errors="replace")
            for hd in args.hyp_dir:
                hf = Path(hd) / rf.name
                if hf.exists():
                    results[f"{hd}/{rf.name}"] = evaluate(ref, hf.read_text(encoding="utf-8", errors="replace"))
        # 디렉터리별 평균
        for hd in args.hyp_dir:
            rows = [v for k, v in results.items() if k.startswith(f"{hd}/")]
            if rows:
                results[f"{hd} [평균]"] = {m: round(sum(r[m] for r in rows if r[m] is not None) /
                                                  max(sum(1 for r in rows if r[m] is not None), 1), 4)
                                          for m in ("CER", "숫자CER", "필드정밀도", "필드재현율", "판독불가율")}
    else:
        ap.print_help()
        return 1

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
    else:
        for name, m in results.items():
            print(f"\n■ {name}")
            for k, v in m.items():
                print(f"   {k:10} {v}")
        if args.ref or args.ref_dir:
            print("\n판정 가이드: 숫자CER·필드재현율이 우선(업무 치명도). "
                  "VLM이 사이냅 대비 숫자CER 같거나 낮고 필드재현율 같거나 높으면 대체 가능 신호.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
