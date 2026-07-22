"""전역 설정. 미확정·임의 가정 값은 전부 여기 격리 - 확정 시 이 파일만 수정."""
import os
from pathlib import Path

ROOT = Path(__file__).parent.parent

# --- DB ---
PG_DSN = os.getenv("PG_DSN", "postgresql://bohun:bohun@localhost:5432/bohun")
# 통계(Text-to-SQL) 전용 읽기전용 접속. 미지정 시 PG_DSN으로 폴백(개발용).
#   폐쇄망 운영: db/schema_readonly.sql로 bohun_ro 롤 생성 후 그 DSN 지정 -> 코드가 뚫려도
#   DB 권한에서 SELECT·통계뷰 외 접근 2차 차단.
PG_DSN_RO = os.getenv("PG_DSN_RO", PG_DSN)

# --- 생성 LLM ---
# LLM_BACKEND: "mock"(기본) | "openai" (OpenAI 호환 API - Ollama 로컬 LLM, FabriX 공통 사용)
#   로컬 LLM(Ollama): LLM_BACKEND=openai FABRIX_ENDPOINT=http://localhost:11434/v1/chat/completions
#                     FABRIX_MODEL=exaone3.5:7.8b (또는 받은 모델명)
# TODO(확인): FabriX 규격 회신 시 아래 값만 실규격으로 교체 (호출 코드는 동일 경로).
LLM_BACKEND = os.getenv("LLM_BACKEND", "mock")
# 사용자 안내 문구에 쓰는 제공자 명칭. 개발(Ollama)=Ollama, 폐쇄망=FabriX 로 세팅하면
# 에러 메시지가 실제 환경에 맞게 표시된다(코드 교체 불필요).
LLM_PROVIDER_LABEL = os.getenv("LLM_PROVIDER_LABEL", "생성 LLM")
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

# --- 에이전트 루프 (v0.2) ---
# 챗봇 반복 검색: 1차 답변이 "확인되지 않습니다"면 질의를 재작성해 재검색 (0=비활성).
#   로컬 7B 기준 재시도 1회당 응답시간이 약 2배 - 폐쇄망 성능에 따라 조정.
CHAT_RETRY_MAX = int(os.getenv("CHAT_RETRY_MAX", "1"))
# 문서 생성 리플렉시온: 검토서·의결서 초안을 근거 자료와 대조 검증 후 지적사항만 수정 (0=비활성).
#   문서 생성은 대화형이 아니므로 지연 허용 폭이 큼 - 기본 1패스.
REFLEXION_MAX_PASSES = int(os.getenv("REFLEXION_MAX_PASSES", "1"))

# --- OCR LLM 검증 (ingestion/verifier.py) ---
# 저신뢰 블록만 FabriX로 교정 - 160만 페이지 전수 LLM 투입은 토큰 비용상 비현실적
VERIFY_CONF_THRESHOLD = 0.85  # TODO(확인): 외부 OCR 솔루션의 confidence 분포 확인 후 조정
VERIFY_ALL = False            # True면 전 블록 검증 (소량·고위험 문서 전용)
