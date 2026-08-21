# -*- coding: utf-8 -*-
"""업무외시간 배치 윈도우 — 무거운 단계(VLM추출·임베딩)를 업무시간 밖으로 미룬다.

BATCH_WINDOW="18:00-08:00" 형식(자정 넘김 허용). 빈값이면 게이트 없음(상시 허용).
BATCH_WEEKEND=1 이면 토·일은 종일 허용. 게이트 대상 단계는 BATCH_STEPS.
설정이 잘못돼도 파이프라인이 서지 않도록 파싱 실패는 '열림'으로 처리한다.
"""
from datetime import datetime, time as dtime

from config.settings import BATCH_WEEKEND, BATCH_WINDOW


def _parse(win: str) -> tuple[dtime, dtime]:
    s, e = win.split("-")
    h1, m1 = s.strip().split(":")
    h2, m2 = e.strip().split(":")
    return dtime(int(h1), int(m1)), dtime(int(h2), int(m2))


def in_batch_window(now: datetime | None = None) -> bool:
    """지금 무거운 배치를 돌려도 되는 시간인가."""
    if not BATCH_WINDOW:
        return True
    now = now or datetime.now()
    if BATCH_WEEKEND and now.weekday() >= 5:
        return True
    try:
        s, e = _parse(BATCH_WINDOW)
    except (ValueError, AttributeError):
        return True
    t = now.time()
    return (s <= t < e) if s <= e else (t >= s or t < e)
