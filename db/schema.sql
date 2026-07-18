-- 보훈심사 AI: PostgreSQL 단일 DB 스키마 v2
CREATE EXTENSION IF NOT EXISTS vector;

-- ===== 비정형: 디지털화 산출물 =====
CREATE TABLE IF NOT EXISTS documents (
  doc_id      BIGSERIAL PRIMARY KEY,
  source_path TEXT NOT NULL,
  doc_type    TEXT,
  sha256      CHAR(64) UNIQUE NOT NULL,
  ocr_engine  TEXT,
  created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chunks (
  chunk_id    BIGSERIAL PRIMARY KEY,
  doc_id      BIGINT REFERENCES documents(doc_id) ON DELETE CASCADE,
  block_type  TEXT NOT NULL,
  content     TEXT NOT NULL,
  page_no     INT,
  embedding   vector(1024),
  content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
  meta        JSONB DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_chunks_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_chunks_tsv  ON chunks USING gin (content_tsv);

-- ===== 정형: 통합보훈시스템 심의체계 (원천: 프로젝트 엑셀 -> db/seed/*.csv) =====
-- TODO(확인): 엑셀은 명칭 기준이라 review_type_name 등을 명칭 TEXT로 저장.
--   실제 통합보훈시스템 DB 연계 시 코드 FK 체계로 전환 검토.
CREATE TABLE IF NOT EXISTS review_type   (code TEXT PRIMARY KEY, name TEXT UNIQUE, in_use BOOLEAN);
CREATE TABLE IF NOT EXISTS exam_category (code TEXT PRIMARY KEY, name TEXT, in_use BOOLEAN);
CREATE TABLE IF NOT EXISTS exam_reason   (id BIGSERIAL PRIMARY KEY, exam_category_name TEXT, reason TEXT, in_use BOOLEAN);
CREATE TABLE IF NOT EXISTS standard_clause (code TEXT PRIMARY KEY, name TEXT, injury_related BOOLEAN, in_use BOOLEAN);
CREATE TABLE IF NOT EXISTS review_content (id BIGSERIAL PRIMARY KEY, review_type_name TEXT, content TEXT, in_use BOOLEAN);
CREATE TABLE IF NOT EXISTS agenda        (id BIGSERIAL PRIMARY KEY, review_type_name TEXT, content TEXT, agenda TEXT, in_use BOOLEAN);

-- 자동생성 주문안: 원본은 와이드(판단내용·결과 쌍 최대 4) -> 쌍 단위 롱 포맷으로 정규화 저장
CREATE TABLE IF NOT EXISTS auto_order_rule (
  id BIGSERIAL PRIMARY KEY,
  rule_no TEXT, review_type_name TEXT, review_content TEXT,
  target_cond TEXT,          -- 예: '' / '1개' / '2개 이상'
  pair_order INT,            -- 주문 출력 순서
  judge_item TEXT,           -- 예: '예우법 4-1-4'
  result TEXT,               -- 예: '해당' / '비해당'
  in_use BOOLEAN
);
CREATE INDEX IF NOT EXISTS idx_aor_match ON auto_order_rule(review_type_name, review_content, target_cond);

CREATE TABLE IF NOT EXISTS kcd (
  id BIGSERIAL PRIMARY KEY,
  disease_name TEXT, kcd_code TEXT, kcd_name TEXT, in_use BOOLEAN
);
CREATE INDEX IF NOT EXISTS idx_kcd_code ON kcd(kcd_code);

-- ===== 심사사례 =====
-- TODO(확인): 실제 심사사례 테이블 구조는 통합보훈시스템 DB 확인 후 확정. 아래는 임의 설계.
CREATE TABLE IF NOT EXISTS cases (
  case_id     BIGSERIAL PRIMARY KEY,
  review_type TEXT,
  review_content TEXT,          -- 심의내용 (예: 상이군경심의) - 주문안 규칙 매칭 키
  exam_category TEXT,
  kcd_codes   TEXT[],
  decision    TEXT,
  decided_at  DATE,
  summary     TEXT,
  summary_embedding vector(1024)
);
CREATE INDEX IF NOT EXISTS idx_cases_hnsw ON cases USING hnsw (summary_embedding vector_cosine_ops);

-- ===== 통계용 읽기전용 뷰 (Text-to-SQL 화이트리스트) =====
-- 뷰 추가 시: 여기 + services/stats.py ALLOWED_VIEWS 두 곳에 등록
CREATE OR REPLACE VIEW v_stats_by_review_type AS
  SELECT review_type, decision, count(*) AS cnt FROM cases GROUP BY 1,2;
CREATE OR REPLACE VIEW v_stats_by_year AS
  SELECT date_part('year', decided_at)::int AS year, decision, count(*) AS cnt
  FROM cases GROUP BY 1,2;
CREATE OR REPLACE VIEW v_stats_by_kcd AS
  SELECT unnest(kcd_codes) AS kcd_code, decision, count(*) AS cnt FROM cases GROUP BY 1,2;

-- 원본 스캔 파일 (명세서 v0.13 09 원문서류 축약): OCR 텍스트와 별개로 스캔 PDF 원본 경로 보관
ALTER TABLE documents ADD COLUMN IF NOT EXISTS orig_path TEXT;
