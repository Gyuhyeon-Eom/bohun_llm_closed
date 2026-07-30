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

# --- 객체 스토리지 (DB정의서 v0.6: 원본은 MinIO, 열람은 presigned URL) ---
# STORAGE_BACKEND: "local"(기본 — 파일시스템, 현행 경로 그대로) | "minio"(운영)
#   minio 전환 시 원본 업로드가 버킷/객체키로 저장되고, 원본 열람(/scan-docs/{id}/file 등)은
#   presigned URL 302 리다이렉트로 전환된다 (화면 코드 수정 불필요).
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")        # host:port (스킴 없이)
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "bohun")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "bohun-secret")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "bohun-origin")
MINIO_SECURE = os.getenv("MINIO_SECURE", "0") == "1"                  # 폐쇄망 내부 http 기본
# 브라우저가 접근하는 외부 주소가 다르면 지정 (도커: 내부 minio:9000 / 외부 localhost:9000)
MINIO_PUBLIC_ENDPOINT = os.getenv("MINIO_PUBLIC_ENDPOINT", "")
PRESIGNED_EXPIRES_S = int(os.getenv("PRESIGNED_EXPIRES_S", "600"))    # 열람 URL 만료(초)

# --- 오케스트레이션 (LangGraph) ---
# ORCH_BACKEND: "plain"(기본 — 현행 절차식 루프) | "langgraph"(그래프 오케스트레이션)
#   langgraph 미반입 환경에서 "langgraph" 지정 시 자동으로 plain 폴백 (기동 실패 없음).
ORCH_BACKEND = os.getenv("ORCH_BACKEND", "plain")

# --- 플랫폼 API (모델·전처리·검색·보안 전부 API 호출 — 260730 요금표 반영) ---
# 생성 모델 2종 라우팅: 심층 생성(의결서·판단)=MAIN, 고빈도·경량(챗봇·요약·OCR정규화)=LIGHT.
#   LLM 모델 기능서 v0.1 배정표 그대로 — 프롬프트명 기준 자동 선택 (LLM_LIGHT_PROMPTS로 조정).
LLM_MODEL_MAIN = os.getenv("LLM_MODEL_MAIN", os.getenv("FABRIX_MODEL", "gpt-oss-120b"))
LLM_MODEL_LIGHT = os.getenv("LLM_MODEL_LIGHT", "") or LLM_MODEL_MAIN   # 예: gemma-4-31b
LLM_LIGHT_PROMPTS = set(filter(None, os.getenv(
    "LLM_LIGHT_PROMPTS",
    "chatbot,query_rewrite,file_notes,similar_reason,ocr_normalize,ocr_verify,eval_judge"
).split(",")))
# --- FabriX OpenAPI 실규격 (docs/vendor/FabriX OpenAPI 매뉴얼.pdf — 260730 확정) ---
# 인증: 모든 OpenAPI 공통 헤더 — x-fabrix-client(클라이언트 키) + x-openapi-token(Bearer 패스키).
#   FABRIX_CLIENT_KEY 설정 = 실규격 모드. 미설정 = 기존 Authorization Bearer (개발용 Ollama/vLLM).
FABRIX_CLIENT_KEY = os.getenv("FABRIX_CLIENT_KEY", "")     # x-fabrix-client
FABRIX_PASS_KEY = os.getenv("FABRIX_PASS_KEY", "")         # x-openapi-token (Bearer 프리픽스 자동 부여)
FABRIX_USER_EMAIL = os.getenv("FABRIX_USER_EMAIL", "")     # x-generative-ai-user-email (선택)
FABRIX_BASE_URL = os.getenv("FABRIX_BASE_URL", "").rstrip("/")  # OpenAPI 신청 시 발급 URL(…/openapi/* 뿌리)
# LLM Serving(§5): OpenAI 호환 /chat/completions — 모델은 x-llm-model-id 헤더(UUID)로 선택,
#   body의 model은 "/mnt/models" 고정. 서비스 필터가 적용되지 않으므로 Security Filter 병행 필수.
FABRIX_MODEL_ID_MAIN = os.getenv("FABRIX_MODEL_ID_MAIN", "")    # gpt-oss-120b modelId (GET /openapi/llm/v1/models)
FABRIX_MODEL_ID_LIGHT = os.getenv("FABRIX_MODEL_ID_LIGHT", "")  # gemma-4-31b modelId
FABRIX_SERVING_MODEL = os.getenv("FABRIX_SERVING_MODEL", "/mnt/models")
# I2T 이미지 분석(§1 messages-with-models — VLM OCR 경로): TEXT+I2T 모델 UUID 2종, 파일 1개/호출
FABRIX_TEXT_MODEL_ID = os.getenv("FABRIX_TEXT_MODEL_ID", "")
FABRIX_I2T_MODEL_ID = os.getenv("FABRIX_I2T_MODEL_ID", "")

# 전처리·검색·보안 API — 미설정 시 FABRIX_BASE_URL에서 실규격 경로 자동 유도, 그것도 없으면
#   해당 기능은 로컬 구현/생략으로 폴백 (기동 불가 없음)
PARSING_API = os.getenv("PARSING_API", "") or (
    f"{FABRIX_BASE_URL}/openapi/parsing/v1/documents/parsing-jobs/files" if FABRIX_BASE_URL else "")
CHUNKING_API = os.getenv("CHUNKING_API", "")          # FabriX Chunking(§12)은 스토리지 사전등록 기반 — 로컬 청커 기본
EMBEDDING_API = os.getenv("EMBEDDING_API", "")        # FabriX엔 텍스트→벡터 API 없음 — bge-m3 로컬 확정(별도 서빙 시만)
RERANK_API = os.getenv("RERANK_API", "")              # FabriX엔 rerank API 없음 — cross-encoder 서빙 확보 시만
SECURITY_FILTER_API = os.getenv("SECURITY_FILTER_API", "") or (
    f"{FABRIX_BASE_URL}/openapi/filter/v1/check" if FABRIX_BASE_URL else "")
KNOWLEDGE_ASSET_ID = os.getenv("KNOWLEDGE_ASSET_ID", "")   # Knowledge 자산 검색(§7) — 자산 등록 시만
PLATFORM_API_KEY = os.getenv("PLATFORM_API_KEY", os.getenv("FABRIX_API_KEY", ""))
API_TIMEOUT_S = int(os.getenv("API_TIMEOUT_S", "60"))
PARSING_TIMEOUT_S = int(os.getenv("PARSING_TIMEOUT_S", "600"))   # 파싱 잡 폴링 최대 대기
PARSING_POLL_S = float(os.getenv("PARSING_POLL_S", "3"))
SECURITY_FILTER_USER_IP = os.getenv("SECURITY_FILTER_USER_IP", "127.0.0.1")  # 필터 이력용(§3 필수 필드)
# LLM 엔드포인트 자동 유도 — 매뉴얼 §5는 "발급 URL + /chat/completions"만 확정. 발급 URL이
#   /openapi/llm/v1 형태가 아니면 FABRIX_ENDPOINT를 직접 지정해 덮어쓴다.
if FABRIX_CLIENT_KEY and FABRIX_BASE_URL and not os.getenv("FABRIX_ENDPOINT"):
    FABRIX_ENDPOINT = f"{FABRIX_BASE_URL}/openapi/llm/v1/chat/completions"
# 보안 필터 적용 지점(in=프롬프트, out=생성문) / STRICT=1이면 필터 장애 시 호출 차단(fail-closed)
SECURITY_FILTER_MODE = os.getenv("SECURITY_FILTER_MODE", "in,out")
SECURITY_FILTER_STRICT = os.getenv("SECURITY_FILTER_STRICT", "0") == "1"
