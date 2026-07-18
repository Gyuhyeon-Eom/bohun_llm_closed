"""통합보훈시스템 심의체계 엑셀 -> CSV 시드 추출.

사용: python3 db/extract_seed.py <엑셀디렉토리>
운영에서는 통합보훈시스템 원천 엑셀이 있는 경로를 지정.
헤더는 각 파일 3행째(제목 2행 스킵) 기준 — 원천 양식이 바뀌면 여기 수정.
"""
import csv, glob, sys, warnings
from pathlib import Path
import openpyxl

warnings.filterwarnings("ignore")
OUT = Path(__file__).parent / "seed"


def rows_of(src_dir: str, key: str, skip: int = 3):
    fs = glob.glob(f"{src_dir}/*{key}*.xlsx")
    if not fs:
        print(f"  [경고] {key} 파일 없음 - 건너뜀"); return []
    ws = openpyxl.load_workbook(fs[0], data_only=True).active
    out = []
    for i, r in enumerate(ws.iter_rows(values_only=True)):
        if i < skip:
            continue
        out.append([("" if c is None else str(c).strip()) for c in r])
    return out


def write(name: str, header: list[str], rows: list[list[str]]):
    with open(OUT / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)
    print(f"  {name}.csv: {len(rows)}행")


def main(src: str):
    OUT.mkdir(exist_ok=True)

    write("review_type", ["code", "name", "in_use"],
          [[r[1], r[2], str(r[3] == "사용")] for r in rows_of(src, "심의유형_목록") if r[1]])

    write("exam_category", ["code", "name", "in_use"],
          [[r[1], r[2], str(r[3] == "사용")] for r in rows_of(src, "심사구분_코드") if r[1]])

    write("exam_reason", ["exam_category_name", "reason", "in_use"],
          [[r[1], r[2], str(r[3] == "사용")] for r in rows_of(src, "심사구분사유") if r[1]])

    write("standard_clause", ["code", "name", "injury_related", "in_use"],
          [[r[1], r[2], str(r[3] == "여"), str(r[4] == "사용")]
           for r in rows_of(src, "표준문안관리") if r[1]])

    write("review_content", ["review_type_name", "content", "in_use"],
          [[r[1], r[2], str(r[3] == "사용")] for r in rows_of(src, "심의내용_목록") if r[1]])

    write("agenda", ["review_type_name", "content", "agenda", "in_use"],
          [[r[1], r[2], r[3], str(r[4] == "사용")] for r in rows_of(src, "심의의제_목록") if r[1]])

    # 병명관리 열: [선행열, 번호, 병명, KCD코드, KCD병명, 등록일, 사용유무]
    write("kcd", ["disease_name", "kcd_code", "kcd_name", "in_use"],
          [[r[2], r[3], r[4], str(r[6] == "사용")] for r in rows_of(src, "병명관리") if r[3]])

    # 자동생성 주문안: 와이드(판단내용·결과 쌍 최대 4개) -> 롱 포맷 정규화
    # 열: [_, 관리번호, 심의유형, 심의내용, 판단대상, 판1, 결1, 판2, 결2, 판3, 결3, 판4, 결4, 사용여부]
    long_rows = []
    for r in rows_of(src, "자동생성_주문안"):
        if not r[1]:
            continue
        for order, (j, res) in enumerate([(r[5], r[6]), (r[7], r[8]), (r[9], r[10]), (r[11], r[12])], 1):
            if j:
                long_rows.append([r[1], r[2], r[3], r[4], str(order), j, res, str(r[13] == "사용")])
    write("auto_order_rule",
          ["rule_no", "review_type_name", "review_content", "target_cond",
           "pair_order", "judge_item", "result", "in_use"], long_rows)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
