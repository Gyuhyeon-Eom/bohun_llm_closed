# -*- coding: utf-8 -*-
"""국가법령정보센터 판례 원시 덤프 → 보훈 관련 판례 필터링 → 적재 형식 변환.

입력: 2025_원시_국가법령정보센터_판례_001/DA01/D1103/<uuid>/<사건명>.json (+_meta.json)
      각 JSON은 {"PrecService": {판시사항, 판결요지, 판례내용, 사건번호, 법원명, 선고일자…}}
출력: data/precedents/bohun_precedents.json — scripts/ingest_precedents.py 입력 형식

사용:
  python3 scripts/filter_law_precedents.py ~/Downloads/2025_원시_국가법령정보센터_판례_001
  python3 scripts/ingest_precedents.py          # 이어서 적재
"""
import argparse
import json
import re
import sys
from pathlib import Path

# 보훈 심사 관련 키워드 — 제목·판시사항·판결요지에서 검사 (전문은 오탐 많아 제외)
KEYWORDS = ("국가유공자", "보훈보상", "보훈심사", "참전유공자", "고엽제",
            "상이등급", "전공사상", "공상군경", "순직군경", "무공수훈",
            "보국수훈", "지원공상", "재해부상군경", "상이연금", "전상군경")
# 확장 키워드 — 전문에만 있어도 인정하되 아래 핵심어와 동반될 때만
SOFT = ("보훈", "유공자")


def match(svc: dict) -> str | None:
    head = " ".join(str(svc.get(k) or "") for k in ("사건명", "판시사항", "판결요지"))
    for kw in KEYWORDS:
        if kw in head:
            return kw
    body = str(svc.get("판례내용") or "")
    for kw in KEYWORDS:
        if kw in body and any(s in head + body for s in SOFT):
            return kw + "(전문)"
    return None


def convert(svc: dict, url: str | None) -> dict:
    gist = " / ".join(
        re.sub(r"<br\s*/?>", " ", str(svc.get(k) or "")).strip()
        for k in ("판시사항", "판결요지") if svc.get(k))
    return {"case_no": f"{svc.get('법원명') or ''} {svc.get('사건번호') or ''}".strip(),
            "court": svc.get("법원명"), "date": str(svc.get("선고일자") or "")[:10],
            "title": svc.get("사건명"), "gist": gist[:2000],
            "body": re.sub(r"<br\s*/?>", "\n", str(svc.get("판례내용") or ""))[:20000],
            "url": url}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump_dir", help="원시 덤프 루트 (…판례_001)")
    ap.add_argument("-o", "--out", default="data/precedents/bohun_precedents.json")
    args = ap.parse_args()

    dirs = [p for p in Path(args.dump_dir).rglob("*") if p.is_dir() and len(p.name) == 32]
    print(f"판례 {len(dirs)}건 스캔 중…")
    hits, kw_stat = [], {}
    for i, d in enumerate(dirs):
        if i % 10000 == 0 and i:
            print(f"  …{i}건 ({len(hits)} 매칭)")
        try:
            main_json = next(f for f in d.glob("*.json") if not f.name.endswith("_meta.json"))
            svc = json.load(open(main_json, encoding="utf-8")).get("PrecService") or {}
        except (StopIteration, json.JSONDecodeError, OSError):
            continue
        kw = match(svc)
        if not kw:
            continue
        url = None
        meta_f = d / (main_json.stem + "_meta.json")
        if meta_f.exists():
            try:
                url = json.load(open(meta_f, encoding="utf-8")).get("url")
            except (json.JSONDecodeError, OSError):
                pass
        hits.append(convert(svc, url))
        kw_stat[kw.replace("(전문)", "")] = kw_stat.get(kw.replace("(전문)", ""), 0) + 1

    hits.sort(key=lambda x: x.get("date") or "", reverse=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(hits, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n보훈 관련 판례 {len(hits)}건 → {out}")
    print("키워드 분포:", dict(sorted(kw_stat.items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    main()
