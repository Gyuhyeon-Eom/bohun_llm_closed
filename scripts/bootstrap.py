# -*- coding: utf-8 -*-
"""반입 후 원터치 부트스트랩 — env·compose만 바꾸면 바로 기동되도록 사전 준비를 자동화.

  python3 scripts/bootstrap.py

수행: DB 기동 대기 → 스키마·룰 일괄 적용(apply_db_updates, 멱등) → 객체 스토리지
버킷 보장 → 환경 요약 출력. 몇 번을 다시 실행해도 안전하며, 상주 워커
(scripts/worker_daemon.py)가 기동할 때마다 자동 호출한다 — 새 환경에서 별도
수작업 없이 compose up만으로 준비가 끝나게 하는 것이 목적.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def wait_db(timeout: int = 180):
    """PG 기동 대기 — compose에서 db 헬스체크 이전에 떠도 스스로 기다린다."""
    import psycopg
    from config.settings import PG_DSN
    t0 = time.time()
    while True:
        try:
            with psycopg.connect(PG_DSN, connect_timeout=3):
                print("DB 연결 확인")
                return
        except Exception as e:
            if time.time() - t0 > timeout:
                raise RuntimeError(f"DB 기동 대기 시간 초과({timeout}초): {e}")
            print(f"DB 대기 중… ({type(e).__name__})")
            time.sleep(2)


def ensure_storage():
    """객체 스토리지 준비 — MinIO 백엔드는 초기화 시 버킷 자동 생성. 실패해도
    local 폴백으로 파이프라인은 동작하므로 경고만 남긴다."""
    try:
        from core.storage import get_storage
        st = get_storage()
        print(f"스토리지 준비 — backend={getattr(st, 'backend', '?')}")
    except Exception as e:
        print(f"스토리지 준비 건너뜀({type(e).__name__}: {e}) — local 폴백으로 동작")


def ensure_dirs():
    """수신함·산출 디렉토리 보장 — 업로드 폴더가 없어서 배치가 헛도는 일 방지."""
    from config.settings import LINK_INBOX, UPLOAD_INBOX
    for d in (LINK_INBOX, UPLOAD_INBOX, "out"):
        os.makedirs(d, exist_ok=True)


def main():
    wait_db()
    import scripts.apply_db_updates as adu
    adu.main()
    ensure_storage()
    ensure_dirs()
    from config.settings import (BATCH_STEPS, BATCH_WINDOW, EMBED_BACKEND,
                                 LINK_POLL_S, LINK_SRC_DSN, LLM_BACKEND)
    print("─" * 50)
    print(f"부트스트랩 완료 — 워커 기동 가능")
    print(f"  연계 원천 : {'실연계 DSN' if LINK_SRC_DSN else '개발 모드(data/link_inbox 폴링)'}")
    print(f"  폴링 주기 : {LINK_POLL_S}초")
    print(f"  배치 윈도우: {BATCH_WINDOW or '상시'} (대상 단계: {','.join(BATCH_STEPS) or '없음'})")
    print(f"  LLM/임베딩: {LLM_BACKEND} / {EMBED_BACKEND}")


if __name__ == "__main__":
    main()
