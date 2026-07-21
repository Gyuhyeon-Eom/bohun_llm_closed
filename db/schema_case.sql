-- 보훈요건심사 DB테이블 명세서 v0.13 기반 사건 스키마 (프로토타입 축약판)
-- 명세서 논리명을 주석으로 병기. 등록자/수정자 감사컬럼은 프로토타입에서 생략.

CREATE TABLE IF NOT EXISTS application (          -- 01 신청로그 + 02 심의목록 축약
  app_id      BIGSERIAL PRIMARY KEY,              -- 일련번호
  recv_no     TEXT UNIQUE,                        -- 접수번호
  applicant   TEXT NOT NULL,                      -- 신청인
  birth_year  INT,                                -- 생년
  duty_type   TEXT,                               -- 복무형태: 병사/부사관/장교/공무원(소방·경찰)
  is_death    BOOLEAN DEFAULT false,              -- 사망 사건 여부
  review_content TEXT,                            -- 심의내용 (상이군경심의 등)
  subcommittee TEXT,                              -- 담당분과 (02 심의목록.담당분과)
  round       INT DEFAULT 1,                      -- 심의차수
  status      TEXT DEFAULT '접수',                 -- 진행상태: 접수/심사중/의결/완료
  apply_story TEXT,                               -- 신청경위 (언제·어디서·무엇을·어떻게·왜)
  aftermath   TEXT,                               -- 현재시점 후유증·합병증
  created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS disability (           -- 03 상이처
  dis_id      BIGSERIAL PRIMARY KEY,
  app_id      BIGINT REFERENCES application(app_id) ON DELETE CASCADE,
  name        TEXT NOT NULL,                      -- 상이처명
  body_side   TEXT,                               -- 신체부위 좌/우/양측
  kcd_code    TEXT,                               -- KCD코드
  onset_ym    TEXT,                               -- 발병년월
  onset_story TEXT,                               -- 발병경위
  fact_date   TEXT, fact_place TEXT, fact_first_dx TEXT  -- 요건사실확인서: 상이연월일/장소/최초부상명
);

CREATE TABLE IF NOT EXISTS medical_record (       -- 04 의료기록
  med_id      BIGSERIAL PRIMARY KEY,
  dis_id      BIGINT REFERENCES disability(dis_id) ON DELETE CASCADE,
  hospital    TEXT,                               -- 의료기관명
  rec_type    TEXT,                               -- 기록구분: 외래/영상/수술/입퇴원/건보내역
  period      TEXT,                               -- 시기구분: 입대전/복무중/전역후
  rec_date    DATE,                               -- 진료일자
  chief       TEXT,                               -- 주호소
  diagnosis   TEXT,                               -- 진단명
  imaging     TEXT,                               -- 영상종류 MRI/CT/X-ray
  finding     TEXT,                               -- 영상소견·수술소견
  chronic     CHAR(1),                            -- 진구성여부 Y/N (급성=N)
  surgery     TEXT,                               -- 수술명
  by_applicant CHAR(1) DEFAULT 'N'                -- 신청인제출여부
);

CREATE TABLE IF NOT EXISTS service_record (       -- 05 병적자료
  svc_id      BIGSERIAL PRIMARY KEY,
  app_id      BIGINT REFERENCES application(app_id) ON DELETE CASCADE,
  enlist_date DATE, discharge_date DATE,          -- 입대일/전역(예정)일
  branch      TEXT,                               -- 병과특기
  career      TEXT,                               -- 근무경력
  leave_note  TEXT,                               -- 휴가내역 (부상일 전후)
  overtime    TEXT                                -- 초과근무·특별업무 (4분과 과로판단)
);

CREATE TABLE IF NOT EXISTS conclusion (           -- 07 결론 (확정 스냅샷) + 06 요건심사 판단축
  con_id      BIGSERIAL PRIMARY KEY,
  app_id      BIGINT REFERENCES application(app_id) ON DELETE CASCADE,
  dis_id      BIGINT REFERENCES disability(dis_id) ON DELETE CASCADE,
  round       INT DEFAULT 1,                      -- 심의차수
  yeu_clause  TEXT, yeu_result TEXT,              -- 국가유공자 축: 조문/해당·비해당
  bosang_clause TEXT, bosang_result TEXT,         -- 보훈보상 축: 조문/해당·비해당
  body_text   TEXT,                               -- 판단내용 (LLM 초안 -> 담당자 수정)
  final_text  TEXT,                               -- 확정결론문안
  status      TEXT DEFAULT '작성중',               -- 작성중/확정
  decided_at  TIMESTAMPTZ,
  UNIQUE (app_id, dis_id, round)
);

-- ── 상이등급심사 (화면설계 v0.4-260714: 목록 -> 상세 -> AI 판정예측) ──
CREATE TABLE IF NOT EXISTS grade_agenda (         -- 등급 재심의 안건
  ga_id       BIGSERIAL PRIMARY KEY,
  agenda_no   TEXT, applicant TEXT, recv_no TEXT, apply_type TEXT,
  body_part   TEXT, injury TEXT,                  -- 신체부위 / 상이처
  base_date   TEXT, exam_dept TEXT,               -- 기준일자 / 신검과목
  grade_date  TEXT, grade_change TEXT,            -- 등급기준일 / 상이등급(기존->재심의)
  ai_summary  TEXT, status TEXT,                  -- 목록: AI 판정근거 요약 / 상태(완료·미흡·부족)
  review_items TEXT[], note_items TEXT[]          -- 검토사항(○) / 비고(◇)
);

CREATE TABLE IF NOT EXISTS grade_case (           -- 과거 상이등급 판정 사례 풀 (유사조회·예측 근거)
  gc_id          BIGSERIAL PRIMARY KEY,
  recv_no        TEXT, meeting_date DATE,
  disease_name   TEXT, body_part TEXT,
  grade          TEXT,                            -- 예: '7급 7124호'
  order_text     TEXT, opinion_text TEXT,         -- 심의회의 의결 주문 / 의견
  name_embedding vector(1024)
);
CREATE INDEX IF NOT EXISTS idx_grade_case_part ON grade_case(body_part);

-- ── 주간보고 확인요청 반영 ──
ALTER TABLE application ADD COLUMN IF NOT EXISTS apply_category TEXT DEFAULT '신규';  -- 5) 보훈심사 구분

CREATE TABLE IF NOT EXISTS feedback (             -- 10) 피드백 의견 게시판
  fb_id      BIGSERIAL PRIMARY KEY,
  parent_id  BIGINT REFERENCES feedback(fb_id) ON DELETE CASCADE,
  author     TEXT,
  content    TEXT NOT NULL,
  page       TEXT,                                -- 작성 위치(안건현황/요건심사 등)
  created_at TIMESTAMPTZ DEFAULT now()
);

-- ── 주간보고 반영(260714): 보훈심사 구분 + 피드백 게시판 ──
ALTER TABLE application ADD COLUMN IF NOT EXISTS apply_kind TEXT DEFAULT '신규';  -- 신규/재신청/이의신청/재심의

CREATE TABLE IF NOT EXISTS feedback (             -- T/F 피드백·의견 게시판
  fb_id      BIGSERIAL PRIMARY KEY,
  parent_id  BIGINT REFERENCES feedback(fb_id) ON DELETE CASCADE,  -- NULL=글, 값=답글
  author     TEXT NOT NULL DEFAULT '익명',
  content    TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_feedback_parent ON feedback(parent_id);

-- ── 상이판정 근거: 시행령 [별표3] 상이등급구분표 (scripts/ingest_grade_criteria.py로 적재) ──
CREATE TABLE IF NOT EXISTS grade_criteria (
  class_no    TEXT PRIMARY KEY,                 -- 분류번호 (예: 7124)
  grade       TEXT NOT NULL,                    -- 상이등급 (예: 7급, 6급2항)
  section     TEXT NOT NULL,                    -- 별표3 절 제목 (예: 팔 및 손가락의 장애)
  body_part   TEXT NOT NULL,                    -- 화면 부위 칩 8종 매핑
  description TEXT NOT NULL,                    -- 신체상이 정도 원문
  embedding   vector(1024)
);
CREATE INDEX IF NOT EXISTS idx_grade_criteria_part ON grade_criteria(body_part);
ALTER TABLE grade_criteria ADD COLUMN IF NOT EXISTS description_tsv tsvector
  GENERATED ALWAYS AS (to_tsvector('simple', body_part || ' ' || description)) STORED;

-- ── 실제 심의의결서 구조 반영(260715): 공적서류 레이어 + 표지 헤더 필드 ──
CREATE TABLE IF NOT EXISTS official_doc (         -- 08 공적서류: 발병경위서·공무상병인증서·군 심사 결정서·사실조회 등
  od_id    BIGSERIAL PRIMARY KEY,
  app_id   BIGINT REFERENCES application(app_id) ON DELETE CASCADE,
  dis_id   BIGINT REFERENCES disability(dis_id) ON DELETE CASCADE,   -- NULL = 사건 공통 서류
  doc_kind TEXT NOT NULL,   -- 발병경위서/공무상병인증서/전공상심사 결정서/사실조회 회신/지휘관 의견서/훈련계획/요양급여내역
  doc_date TEXT, issuer TEXT,
  content  TEXT
);
CREATE INDEX IF NOT EXISTS idx_official_doc_app ON official_doc(app_id);

ALTER TABLE application ADD COLUMN IF NOT EXISTS assignee TEXT;        -- 담당자 (표지)
ALTER TABLE application ADD COLUMN IF NOT EXISTS assigned_date TEXT;   -- 배정일 (표지)
ALTER TABLE application ADD COLUMN IF NOT EXISTS track TEXT DEFAULT '일반';  -- 심사구분: 일반/패스트트랙

-- ── 상이등급심사 진행상태 + 작업로그 (DAG 시각화, 260716) ──
ALTER TABLE grade_agenda ADD COLUMN IF NOT EXISTS progress   TEXT DEFAULT '접수';
ALTER TABLE grade_agenda ADD COLUMN IF NOT EXISTS assignee   TEXT;
ALTER TABLE grade_agenda ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();

CREATE TABLE IF NOT EXISTS grade_log (
  gl_id      BIGSERIAL PRIMARY KEY,
  ga_id      BIGINT REFERENCES grade_agenda(ga_id) ON DELETE CASCADE,
  step       TEXT NOT NULL,           -- 접수/자료수집/AI예측/검토/의결/완료
  event      TEXT NOT NULL,
  actor      TEXT,                    -- 담당자명/AI/시스템
  detail     TEXT,
  file_name  TEXT,
  status     TEXT DEFAULT 'done',     -- done/running/failed/pending
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_grade_log_agenda ON grade_log(ga_id);

-- ── 피드백 게시판 확장 (목업 v0.1 양식) ──
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS org        TEXT;
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS dept       TEXT;
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS bunkwa     TEXT;
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS writer     TEXT;
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS menu       TEXT;
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS screen     TEXT;
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS area       TEXT;
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS vtype      TEXT;
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS importance TEXT DEFAULT 'm';
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS status     TEXT DEFAULT '접수';
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS status_note TEXT;
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS proposal   JSONB;
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS likes      INT DEFAULT 0;
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS kind       TEXT DEFAULT 'opinion';
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS qa_context TEXT;
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS answer_pos TEXT;
ALTER TABLE feedback ADD COLUMN IF NOT EXISTS target     TEXT;
CREATE INDEX IF NOT EXISTS idx_feedback_kind ON feedback(kind);

-- ── 상이등급 신체검사 실측·소견 데이터 (양식 사진 전체 컬럼 반영, 260716) ──
ALTER TABLE grade_agenda ADD COLUMN IF NOT EXISTS resident_no   TEXT;   -- 주민등록번호(마스킹)
ALTER TABLE grade_agenda ADD COLUMN IF NOT EXISTS target_type   TEXT;   -- 대상구분(공상군경/재해부상 등)
ALTER TABLE grade_agenda ADD COLUMN IF NOT EXISTS yeu_injury    TEXT;   -- 요건인정 상이처
ALTER TABLE grade_agenda ADD COLUMN IF NOT EXISTS direct_review TEXT;   -- 직접심의 여부
ALTER TABLE grade_agenda ADD COLUMN IF NOT EXISTS exam_grade    TEXT;   -- 신검등급(신체검사 판정 등급)
ALTER TABLE grade_agenda ADD COLUMN IF NOT EXISTS specialist_opinion TEXT;  -- 보훈병원 전문의 소견
ALTER TABLE grade_agenda ADD COLUMN IF NOT EXISTS route_note    TEXT;   -- 경로사항(진단서·진단일·병명 등)
ALTER TABLE grade_agenda ADD COLUMN IF NOT EXISTS measurements  JSONB;  -- 부위별 실측치 [{name, value, unit, ref, result}]
ALTER TABLE grade_agenda ADD COLUMN IF NOT EXISTS related_docs  TEXT[]; -- 관련자료(제출서류 목록)

-- ── 상이등급 상세 서사 데이터 (실제 심사문서 수준, 260716) ──
ALTER TABLE grade_agenda ADD COLUMN IF NOT EXISTS med_timeline JSONB;   -- 의무기록 시간순 [{date,hospital,type,dx,finding}]
ALTER TABLE grade_agenda ADD COLUMN IF NOT EXISTS prior_history TEXT;   -- 이전 판정·재심의 경위(장문)
ALTER TABLE grade_agenda ADD COLUMN IF NOT EXISTS past_history TEXT;    -- 과거력·기왕증
ALTER TABLE grade_agenda ADD COLUMN IF NOT EXISTS onset_narrative TEXT; -- 상이 발생경위 서사(장문)

-- ── AI챗봇 세션·대화 (DB명세서 v0.13 20·21 축약. 22 챗봇근거는 sources JSONB로 통합) ──
CREATE TABLE IF NOT EXISTS chat_session (
  cs_id      BIGSERIAL PRIMARY KEY,
  channel    TEXT DEFAULT '내부',              -- 채널구분 (내부/대국민 — 프로토타입은 내부만)
  title      TEXT,                             -- 세션 제목 = 첫 질문 요약
  started_at TIMESTAMPTZ DEFAULT now(),
  last_at    TIMESTAMPTZ DEFAULT now()         -- 목록 정렬용 최근 대화 시각
);
CREATE TABLE IF NOT EXISTS chat_message (
  cm_id      BIGSERIAL PRIMARY KEY,
  cs_id      BIGINT REFERENCES chat_session(cs_id) ON DELETE CASCADE,
  seq        INT,                              -- 세션 내 대화순번
  role       TEXT NOT NULL,                    -- user | ai
  content    TEXT NOT NULL,
  sources    JSONB,                            -- RAG 근거 [{source_path,page_no,content,...}] (22 축약)
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_chat_message_cs ON chat_message(cs_id, seq);

-- ── 심사표 확정 양식(260720): 안건당 요건인정 상이처 복수 지원 ──
-- 상이처별로 직전등급→신검과목→신검등급→소견→제안등급을 매기고 종합 제안등급을 산출하는 로직 반영.
-- [{injury, prev_grade, exam_dept, exam_grade, opinion}] — 미설정 시 기존 단일 컬럼(yeu_injury 등)으로 대체.
ALTER TABLE grade_agenda ADD COLUMN IF NOT EXISTS injury_items JSONB;

-- ── 신체검사 서류 스캔본 OCR 적재 (260720): 스캔 PDF → OCR → 정형 파싱 → 사건 변환 ──
-- scripts/ocr_ingest_scans.py 가 적재, services/scan_to_case.py 가 application으로 변환
CREATE TABLE IF NOT EXISTS scan_doc (
  sd_id      BIGSERIAL PRIMARY KEY,
  reg_no     TEXT,                        -- 병원 등록번호 (문서 헤더 OCR)
  person     TEXT,                        -- 성명 (문서 헤더 OCR)
  sex_age    TEXT,                        -- 성별/나이
  hospital   TEXT,                        -- 발행기관 (보훈병원 등, 미검출 시 NULL)
  doc_kind   TEXT,                        -- 문서 종류 (영상검사결과 등)
  file_name  TEXT UNIQUE,                 -- 원본 파일명 (재적재 시 대체 기준)
  orig_path  TEXT,                        -- 보존된 원본 PDF 경로 (출처 열람용)
  pages      INT,
  ocr_used   BOOLEAN DEFAULT false,       -- true=이미지 OCR / false=텍스트층 추출
  raw_text   TEXT,                        -- 전체 추출 텍스트 (페이지 \f 구분)
  exams      JSONB,                       -- 검사 블록 [{page,req_date,exam_date,read_date,dx,exam_name,finding,conclusion,recommendation,reader}]
  app_id     BIGINT,                      -- 사건 변환 시 연결된 application
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_scan_doc_person ON scan_doc(person, reg_no);

-- ── 실데이터 표시 (260720): 시연용 표본과 구분 — 화면에 파란 '실데이터' 배지 ──
ALTER TABLE scan_doc    ADD COLUMN IF NOT EXISTS is_real BOOLEAN DEFAULT false;
ALTER TABLE application ADD COLUMN IF NOT EXISTS is_real BOOLEAN DEFAULT false;

-- ── 분과별 판단기준 룰 테이블 (260720): 정형화틀 v2.4 모듈 시트의 구조화 적재 ──
-- 평문 프롬프트(subcommittee_modules)와 달리 기계 대조 가능: 필요서류 보유 대조,
-- 계산 가능한 조건(MRI 3개월 등)은 services/rule_check.py 가 결정적으로 판정.
CREATE TABLE IF NOT EXISTS judgment_rule (
  jr_id           BIGSERIAL PRIMARY KEY,
  subcommittee    TEXT NOT NULL,             -- 분과번호 1~5
  disease_pattern TEXT NOT NULL,             -- 질환 매칭 정규식 (상이처명·심의내용 대상)
  axis            TEXT NOT NULL,             -- 판단축 (예: 급성/진구성 분기)
  condition       TEXT NOT NULL,             -- 조건 서술 (v2.4 원문 기반)
  check_kind      TEXT NOT NULL DEFAULT 'manual',  -- auto(계산)/doc(서류대조)/manual(담당자)
  required_docs   TEXT[],                    -- 필요서류 키워드 (보유 자료 대조용)
  basis           TEXT                       -- 근거 (v2.4 시트 위치·의안번호 등)
);
CREATE INDEX IF NOT EXISTS idx_judgment_rule_sub ON judgment_rule(subcommittee);

-- ── 실데이터 스캔 → 상이등급 안건 연결 (260721): 신검 서류는 등급심사 흐름으로 ──
ALTER TABLE grade_agenda ADD COLUMN IF NOT EXISTS is_real BOOLEAN DEFAULT false;
ALTER TABLE scan_doc     ADD COLUMN IF NOT EXISTS ga_id   BIGINT;   -- 등급 안건 변환 시 연결
