# -*- coding: utf-8 -*-
"""pytest 수집 설정.

test_graph_rag.py는 단독 실행형 스크립트(`python3 tests/test_graph_rag.py`,
print 체크 + 모듈 레벨 sys.exit)라 pytest 수집 시 세션이 중단된다 — 수집에서 제외.
"""
collect_ignore = ["test_graph_rag.py"]
