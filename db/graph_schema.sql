-- 경량 지식그래프: 기존 시드 테이블에서 build_graph.py가 재생성하는 파생 계층.
-- 원천은 시드·법령이며 그래프는 언제든 지우고 다시 만들 수 있다 (동기화 문제 원천 차단).
CREATE TABLE IF NOT EXISTS kg_nodes (
  node_id BIGSERIAL PRIMARY KEY,
  ntype   TEXT NOT NULL,   -- review_type / review_content / agenda / clause / kcd / case
  key     TEXT NOT NULL,   -- 자연키 (예: clause='예우법 4-1-4', kcd='M21.27', case='17')
  name    TEXT,
  meta    JSONB DEFAULT '{}',
  UNIQUE (ntype, key)
);
CREATE TABLE IF NOT EXISTS kg_edges (
  edge_id BIGSERIAL PRIMARY KEY,
  src     BIGINT REFERENCES kg_nodes(node_id) ON DELETE CASCADE,
  dst     BIGINT REFERENCES kg_nodes(node_id) ON DELETE CASCADE,
  etype   TEXT NOT NULL,   -- HAS_CONTENT / HAS_AGENDA / APPLIES / HAS_KCD / OF_TYPE
  meta    JSONB DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_kg_edges_src ON kg_edges(src, etype);
CREATE INDEX IF NOT EXISTS idx_kg_edges_dst ON kg_edges(dst, etype);
