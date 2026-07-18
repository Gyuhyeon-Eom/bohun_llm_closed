"""저품질 OCR 목데이터 생성기.

시드 실데이터(심의내용·조문·KCD 병명)를 조합해 심사문서를 합성한 뒤,
한국어 OCR 전형 오류를 확률 주입한다. 블록별 confidence는 주입 강도에 반비례.
산출: mockgen/corpus/*.json  (외부 OCR 산출물 형태 = {"blocks":[{type,text,page,confidence}]})

주입하는 오류 패턴 (실제 한국어 OCR 오인식 사례 기반):
  형태 유사 치환(병->명, 0->O, 1->l, 예->애 ...), 조문번호 훼손(4-1-4 -> 4-l-4),
  띄어쓰기 붕괴/과다, 문장 중간 줄바꿈, 글자 탈락, 노이즈 문자 삽입
TODO(확인): 실제 외부 OCR 오류 분포 확보 시 SUBS·확률을 실측 기반으로 교정할 것.
"""
import json, random, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import psycopg
from config.settings import PG_DSN

OUT = Path(__file__).parent / "corpus"
SUBS = {"병": "명", "명": "멍", "예": "애", "법": "벌", "훈": "흔", "조": "초",
        "제": "재", "항": "힝", "심": "싱", "의": "익", "등": "둥", "상": "삼",
        "0": "O", "1": "l", "8": "B", "5": "S", "-": "ㅡ", ".": ",", "국": "극"}
NOISE = list("·¸'˙‚ˈ|")
SEVERITY = {"high": 0.15, "mid": 0.07, "low": 0.02}


def corrupt(text: str, p: float, rng: random.Random) -> str:
    out = []
    for ch in text:
        r = rng.random()
        if r < p * 0.45 and ch in SUBS:
            out.append(SUBS[ch])                       # 형태 유사 치환
        elif r < p * 0.55:
            continue                                   # 글자 탈락
        elif r < p * 0.70 and ch != "\n":
            out.append(ch + rng.choice(NOISE))          # 노이즈 삽입
        elif r < p * 0.85 and ch == " ":
            continue                                   # 띄어쓰기 붕괴
        elif r < p and ch != "\n":
            out.append(ch + ("\n" if rng.random() < 0.3 else " "))  # 과다 공백/중간 줄바꿈
        else:
            out.append(ch)
    return "".join(out)


def fetch_material(limit: int):
    """시드에서 (심의내용, 조문+결과, KCD) 재료 수집."""
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("""
        SELECT r.review_content, r.judge_item, r.result, k.kcd_code, k.disease_name
        FROM (SELECT DISTINCT review_content, judge_item, result FROM auto_order_rule WHERE in_use) r
        CROSS JOIN LATERAL (SELECT kcd_code, disease_name FROM kcd WHERE in_use
                            ORDER BY md5(kcd_code || r.judge_item) LIMIT 1) k
        ORDER BY md5(r.review_content || r.judge_item) LIMIT %s""", (limit,))
        return cur.fetchall()


def make_doc(i, content, clause, result, kcd, disease):
    return [
        {"type": "heading", "text": f"{content} 검토자료 제{i+1}호"},
        {"type": "paragraph", "text":
            f"신청인은 군 복무 중 공무수행으로 {disease}(질병코드 {kcd}) 상이를 입었다고 "
            f"주장하며 {content}을 신청하였다. 제출된 병상일지와 의무기록을 검토한 결과, "
            f"발병 경위와 공무 관련성에 대한 진술이 확인된다."},
        {"type": "paragraph", "text":
            f"관련 법령을 검토하면, {clause}의 요건 충족 여부가 판단 대상이 된다. "
            f"심의 결과 본 건은 {clause} {result}으로 판단됨이 상당하다."},
        {"type": "table", "text":
            f"판단대상 | 판단내용 | 결과\n{disease}({kcd}) | {clause} | {result}"},
    ]


def main(n_docs: int = 24, seed: int = 42):
    rng = random.Random(seed)
    OUT.mkdir(exist_ok=True)
    materials = fetch_material(n_docs)
    qa = []
    for i, (content, clause, result, kcd, disease) in enumerate(materials):
        sev = ["high", "mid", "low"][i % 3]
        p = SEVERITY[sev]
        blocks = []
        for b in make_doc(i, content, clause, result, kcd, disease):
            bp = p * rng.uniform(0.6, 1.4)
            blocks.append({**b, "page": 1 if b["type"] != "table" else 2,
                           "text": corrupt(b["text"], bp, rng),
                           "confidence": round(max(0.3, 1 - bp * 4 + rng.uniform(-.05, .05)), 3)})
        doc = {"doc_id": f"mock_{i:03d}", "severity": sev, "blocks": blocks,
               "truth": {"clause": clause, "kcd": kcd, "disease": disease, "result": result}}
        (OUT / f"mock_{i:03d}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))
        qa.append({"q": f"{disease} 관련 심의 결과는?", "expect_doc": f"mock_{i:03d}"})
        qa.append({"q": f"{clause} 적용 검토 사례", "expect_doc": f"mock_{i:03d}"})
    (OUT / "qa_set.json").write_text(json.dumps(qa, ensure_ascii=False, indent=1))
    print(f"목데이터 {len(materials)}건 생성 (강도 high/mid/low 순환), QA {len(qa)}건 -> {OUT}")


if __name__ == "__main__":
    main()
