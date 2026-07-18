"""시연용 안건(cases) 시더 - 화면(안건 목록·의결서)의 재료.

실 KCD·심의내용을 조합한 안건 8건. decision NULL = 심의대기.
멱등: demo 표식 안건만 지우고 재생성. 실환경에서는 통합보훈시스템 연계로 대체.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import psycopg
from config.settings import PG_DSN
from ingestion.embedder import get_embedder

CASES = [
    ("요건심의", "상이군경심의",   "0", ["M21.27"],          "김O수", "혹한기 훈련 중 낙상으로 좌측 족지 굴곡변형 상이. 병상일지상 3주 입원 기록 확인."),
    ("요건심의", "상이공무원심의", "0", ["M51.2"],           "이O정", "재난 구조 공무수행 중 요추 추간판탈출증 발병 주장. 공무상 재해 인정 여부 검토 필요."),
    ("요건심의", "사망군경심의",   "0", ["S06.9"],           "박O철", "야간 경계근무 중 사고로 두부 손상 후 사망. 순직군경 요건 해당 여부."),
    ("고엽제심의", "고엽제후유증심의", "0", ["E11.2"],       "최O호", "베트남전 참전. 당뇨병성 신장질환 진단, 후유증 질병 목록 해당 여부."),
    ("요건심의", "상이군경심의",   "2", ["M21.27", "M51.2"], "정O아", "재신청 건. 기존 비해당 판정 후 신규 의무기록 추가 제출. 판단대상 2건."),
    ("보상심의", "상이군경심의",   "0", ["H90.3"],           "한O민", "포병 복무 중 소음성 난청. 보상법 적용 대상 여부 검토."),
    ("요건심의", "상이공무원심의", "3", ["S82.1"],           "오O진", "추가 상이처 신청. 하퇴골 골절 후유장애."),
    ("고엽제심의", "고엽제후유의증심의", "0", ["I25.1"],     "서O래", "허혈성 심장질환. 후유의증 해당 여부 및 장애등급 판정 필요."),
]


def main():
    emb = get_embedder()
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM cases WHERE summary LIKE '[demo]%'")
        for rt, rc, ec, kcds, name, summary in CASES:
            vec = emb.encode([f"{rc} {' '.join(kcds)} {summary}"])[0]
            cur.execute(
                "INSERT INTO cases(review_type, review_content, exam_category, kcd_codes,"
                " decision, summary, summary_embedding) VALUES (%s,%s,%s,%s,NULL,%s,%s)",
                (rt, rc, ec, kcds, f"[demo]{name}|{summary}", vec))
        cur.execute("SELECT count(*) FROM cases WHERE summary LIKE '[demo]%'")
        print(f"데모 안건 {cur.fetchone()[0]}건 생성")


if __name__ == "__main__":
    main()
