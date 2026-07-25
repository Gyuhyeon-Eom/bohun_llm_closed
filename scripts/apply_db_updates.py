# -*- coding: utf-8 -*-
"""코드 반영 후 DB 갱신 일괄 실행 — 260724 (정형화틀 최종본 v2 + 게시판 초기화).

git pull로 코드를 받아도 DB 상태(판단기준 룰·그래프·게시판)는 이 스크립트를
돌려야 바뀐다. 폐쇄망에서도 그대로 실행 가능 (외부 다운로드 없음).

실행:
  python3 scripts/apply_db_updates.py                # 룰 36건 재적재 + 그래프 재구축
  python3 scripts/apply_db_updates.py --clear-board  # + 게시판(의견·확인필요) 전체 초기화

수행 내용:
  1. judgment_rule 재적재 (정형화틀 최종본 v2 — 27→36건)
  2. 판단기준 그래프(jr_*) 재구축
  3. 온톨로지 인스턴스 레이어(person·disease·안건·스캔) 재구축 — 그래프 RAG용
  4. (--clear-board) feedback 게시판 초기화 — 샘플 시드는 코드에서 이미 제거되어
     재시드로 되살아나지 않는다
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    clear_board = "--clear-board" in sys.argv

    print("① 판단기준 룰 재적재 (정형화틀 최종본 v2)")
    import scripts.seed_judgment_rules as sjr
    sjr.main()

    print("② 판단기준 그래프(jr_*) 재구축")
    import scripts.build_rule_graph as brg
    brg.main()

    print("③ 온톨로지 인스턴스 레이어 재구축 (그래프 RAG)")
    try:
        import db.build_instance_graph as big
        big.main()
    except Exception as e:  # 인스턴스 스키마 미반입 환경에서도 앞 단계는 유효
        print(f"   건너뜀 — {e}")

    if clear_board:
        print("④ 게시판(의견·확인 필요사항) 초기화")
        import psycopg
        from config.settings import PG_DSN
        with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM feedback")
            n = cur.fetchone()[0]
            cur.execute("TRUNCATE feedback RESTART IDENTITY CASCADE")
            conn.commit()
        print(f"   삭제 {n}건 — 이후 등록되는 의견부터 실데이터로 축적")
    else:
        print("④ 게시판은 유지 (초기화하려면 --clear-board)")
    print("완료 — 서버 재시작 후 브라우저 강력 새로고침(Ctrl+Shift+R)하면 화면에 반영됩니다.")


if __name__ == "__main__":
    main()
