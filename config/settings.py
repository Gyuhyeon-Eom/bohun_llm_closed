"""전역 설정. 미확정·임의 가정 값은 전부 여기 격리 - 확정 시 이 파일만 수정."""
import os
from pathlib import Path

ROOT = Path(__file__).parent.parent

# --- DB ---
PG_DSN = os.getenv("PG_DSN", "postgresql://bohun:bohun@localhost:5432/bohun")

# --- 생성 LLM ---
# LLM_BACKEND: "mock"(기본) | "openai" (OpenAI 호환 API - Ollama 로컬 LLM, FabriX 공통 사용)
#   로컬 LLM(Ollama): LLM_BACKEND=openai FABRIX_ENDPOINT=http://localhost:11434/v1/chat/completions
#                     FABRIX_MODEL=exaone3.5:7.8b (또는 받은 모델명)
# TODO(확인): FabriX 규격 회신 시 아래 값만 실규격으로 교체 (호출 코드는 동일 경로).
LLM_BACKEND = os.getenv("LLM_BACKEND", "mock")
FABRIX_ENDPOINT = os.getenv("FABRIX_ENDPOINT", "http://localhost:11434/v1/chat/completions")
FABRIX_API_KEY = os.getenv("FABRIX_API_KEY", "ollama")   # Ollama는 아무 값이나 허용
FABRIX_MODEL = os.getenv("FABRIX_MODEL", "exaone3.5:7.8b")
LLM_TIMEOUT_S = int(os.getenv("LLM_TIMEOUT_S", "120"))   # 로컬 7B는 여유 있게
LLM_MAX_RETRIES = 2
LLM_TEMPERATURE = 0.2   # 행정문서 특성상 낮게. TODO(확인): 기능별 조정 여지

# --- 임베딩 ---
# EMBED_BACKEND: "bge"(운영, bge-m3 실모델) | "hash"(개발용 대체 - 모델 없이 파이프라인 검증)
EMBED_BACKEND = os.getenv("EMBED_BACKEND", "bge")
# 온라인: "BAAI/bge-m3" (HF 자동 다운로드) / 폐쇄망: 반입한 모델 디렉토리 절대경로
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-m3")
EMBED_DEVICE = os.getenv("EMBED_DEVICE", "")   # ""=자동(GPU 있으면 cuda), "cpu" 강제 가능
EMBED_DIM = 1024          # pgvector vector(1024)와 일치. 변경 시 전체 재임베딩 필요
EMBED_BATCH = 32

# --- 청킹 ---
CHUNK_MAX_CHARS = 1200    # TODO(확인): 샘플 데이터 투입 후 조정
CHUNK_OVERLAP_CHARS = 150 # TODO(확인): 샘플 데이터 투입 후 조정

# --- 검색 ---
TOP_K = 5
RRF_K = 60
# RRF 가중: 벤치마크 실측(bge-m3, 오염 코퍼스)에서 hybrid < dense 단독 확인 ->
# dense 우위 가중이 기본. 정상 판독 실코퍼스로 재측정 후 조정할 것.
RRF_DENSE_WEIGHT = float(os.getenv("RRF_DENSE_WEIGHT", "1.0"))
RRF_SPARSE_WEIGHT = float(os.getenv("RRF_SPARSE_WEIGHT", "0.5"))

# --- OCR LLM 검증 (ingestion/verifier.py) ---
# 저신뢰 블록만 FabriX로 교정 - 160만 페이지 전수 LLM 투입은 토큰 비용상 비현실적
VERIFY_CONF_THRESHOLD = 0.85  # TODO(확인): 외부 OCR 솔루션의 confidence 분포 확인 후 조정
VERIFY_ALL = False            # True면 전 블록 검증 (소량·고위험 문서 전용)
