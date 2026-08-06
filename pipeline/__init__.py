"""보훈심사 LLM 파이프라인 — 폐쇄망 운영 3플로우 (웹 불필요).
① flow_ingest  스캔→VLM 전사→DB 적재→RAG 색인
② flow_decision 요건심사 심의의결서 초안 생성
③ flow_grade   상이등급 심사표 XLSX
진입점: python -m pipeline {ingest|decision|grade|check}
"""
