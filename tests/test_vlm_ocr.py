# -*- coding: utf-8 -*-
"""VLM 전사 후처리 회귀 — 04판독지 실측(p3 숫자CER 6배 부풀림)에서 도출한 개선 검증."""
from scripts.vlm_ocr import clean_transcript, merge_tiles, sanity


def test_merge_tiles_dedups_whitespace_variants():
    """겹침 구간 같은 줄이 공백만 다르게 전사돼도 중복 제거 (실측 p3 사례)."""
    t1 = "임상진단명 :\n( 의뢰일 : 2026-06-24 / 검사일 : 11:24 )"
    t2 = "(의뢰일: 2026-06-24 / 검사일: 11:24 )\n검사명 :\nKnee AP"
    merged = merge_tiles([t1, t2])
    assert merged.count("2026-06-24") == 1
    assert "검사명" in merged and "임상진단명" in merged


def test_merge_tiles_keeps_distant_legit_repeats():
    """문서상 정당한 반복(다른 섹션의 같은 줄)은 겹침이 아니므로 유지."""
    t1 = "판독의 : 김재원(85896)\nA\nB\nC\nD\nE\nF\nG"
    t2 = "H\nI\n판독의 : 김재원(85896)"
    merged = merge_tiles([t1, t2])
    assert merged.count("김재원") == 2


def test_sanity_flags_adjacent_duplicate():
    """근접(3줄 이내) 동일 줄(공백 무시) → 타일 이음새 중복 경고."""
    text = "임상진단명 :\n( 의뢰일 : 2026-06-24 11:24 )\n일상진단명\n(의뢰일: 2026-06-24 11:24 )"
    warns = sanity(text)
    assert any("근접 중복" in w for w in warns)


def test_sanity_ignores_distant_repeats():
    lines = ["( 의뢰일 : 2026-06-24 11:24 )"] + [f"내용 {i}" for i in range(6)] \
        + ["( 의뢰일 : 2026-06-24 11:24 )"]
    assert not any("근접 중복" in w for w in sanity("\n".join(lines)))


def test_clean_transcript_preserved():
    """기존 동작 회귀 — 필러 제거·연속 동일 줄 축약은 그대로."""
    out = clean_transcript("성명: 홍길동\n....\n....\n같은줄\n같은줄\n같은줄\n같은줄")
    assert out.splitlines().count("같은줄") == 2 and "...." not in out
