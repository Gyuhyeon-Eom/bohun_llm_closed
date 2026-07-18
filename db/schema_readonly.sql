-- ============================================================================
-- 통계(Text-to-SQL) 전용 읽기전용 롤. services/stats.py가 PG_DSN_RO로 접속한다.
--
-- 목적(폐쇄망·개인의료정보 2차 방어): 애플리케이션의 SQL 검증(sqlglot)이 뚫리더라도
--   DB 권한 자체에서 (1) 쓰기 불가, (2) 통계 뷰 외 테이블 접근 불가 를 보장한다.
--
-- 적용:
--   psql "$PG_DSN" -f db/schema_readonly.sql          # 슈퍼유저/소유주로 실행
--   export PG_DSN_RO="postgresql://bohun_ro:<암호>@localhost:5432/bohun"
--   (미설정 시 stats.py는 PG_DSN으로 폴백 — 개발 편의용, 운영에선 반드시 분리)
--
-- 주의: 통계 뷰(v_stats_*)를 db/schema.sql에 추가/변경했다면 이 파일도 함께 갱신해
--   새 뷰에 GRANT SELECT를 준다(services/stats.py ALLOWED_VIEWS와도 동기화).
-- ============================================================================

-- 1) 롤 생성 (이미 있으면 암호만 갱신). 운영에서는 아래 '변경필요'를 실제 암호로.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bohun_ro') THEN
    CREATE ROLE bohun_ro LOGIN PASSWORD '변경필요';
  END IF;
END $$;

-- 2) 기본 권한 회수: public 스키마의 모든 테이블/뷰에 대한 접근을 우선 전부 제거
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM bohun_ro;
REVOKE ALL ON SCHEMA public FROM bohun_ro;

-- 3) 스키마 사용(객체 조회)만 허용 + 통계 뷰에만 SELECT 부여
GRANT USAGE ON SCHEMA public TO bohun_ro;
GRANT SELECT ON
  v_stats_by_review_type,
  v_stats_by_year,
  v_stats_by_kcd,
  v_stats_by_status,
  v_stats_by_subcommittee,
  v_stats_conclusion
TO bohun_ro;

-- 4) 앞으로 생성될 테이블에 대한 기본 권한도 자동 부여되지 않도록(안전 기본값)
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM bohun_ro;
