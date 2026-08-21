# -*- coding: utf-8 -*-
"""상주 워커 데몬 — 부트스트랩(멱등) 후 연계 워커를 상주 실행. compose worker 서비스 진입점.

  python3 scripts/worker_daemon.py                        # 전 단계 담당 워커 1기
  python3 scripts/worker_daemon.py --steps 수집,VLM추출    # 파일 처리 전담 워커
  python3 scripts/worker_daemon.py --steps 임베딩,초안생성,전송   # 적재·생성 전담 워커

동작: 기동 시 scripts/bootstrap.py를 먼저 수행(DB 대기·스키마·스토리지 — 멱등)하고
연계 워커 루프(pipeline/flow_link.run)로 진입한다. 업무외시간 게이트(BATCH_WINDOW)는
flow_link 안에서 적용되므로 데몬은 시간대와 무관하게 상시 떠 있으면 된다:
업무시간에는 수집·경량 단계만, 윈도우가 열리면 VLM추출·임베딩·업로드 수신함까지 소화.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    ap = argparse.ArgumentParser(description="연계 상주 워커 (부트스트랩 포함)")
    ap.add_argument("--steps", help="담당 단계 CSV (예: 수집,VLM추출). 미지정=전 단계")
    ap.add_argument("--interval", type=int, help="폴링 주기 초 (기본 LINK_POLL_S)")
    ap.add_argument("--no-bootstrap", action="store_true",
                    help="부트스트랩 생략 (이미 준비된 환경에서 재기동 시간 단축)")
    a = ap.parse_args()

    if not a.no_bootstrap and os.getenv("SKIP_BOOTSTRAP") != "1":
        import scripts.bootstrap as bs
        bs.main()

    steps = tuple(s.strip() for s in a.steps.split(",") if s.strip()) if a.steps else None
    from pipeline import flow_link
    flow_link.run(interval=a.interval, steps=steps)


if __name__ == "__main__":
    main()
