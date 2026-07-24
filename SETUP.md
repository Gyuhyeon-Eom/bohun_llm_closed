# SETUP — 실환경 구축·실측 가이드

목표: 이 레포를 실환경에 올려 **bge-m3 실모델로 벤치마크를 돌리고 실성능 수치를 얻는 것**.
샌드박스(모델 다운로드 불가)에서는 파이프라인 동작까지만 검증된 상태이며,
성능 수치는 아래 절차로 각자 환경에서 측정한다.

## 0. 요구사항

- Ubuntu 22.04/24.04 (RHEL 계열도 무방, 패키지 명령만 대체)
- Python 3.10+
- PostgreSQL 16 + pgvector
- 디스크: 모델 ~2.3GB + DB 여유
- GPU 선택사항 — bge-m3는 CPU로도 동작(임베딩 속도만 차이). 벤치마크가 처리량을 실측해준다.

## 1. PostgreSQL 16 + pgvector

```bash
sudo apt update
sudo apt install -y postgresql postgresql-server-dev-16 build-essential git
git clone --depth 1 https://github.com/pgvector/pgvector.git && cd pgvector
make && sudo make install
sudo service postgresql start
sudo -u postgres psql -c "CREATE USER bohun WITH PASSWORD 'bohun' SUPERUSER"
sudo -u postgres createdb -O bohun bohun
```
폐쇄망: pgvector 소스 tarball을 반입해 동일하게 make. (별도 바이너리 불필요)

## 2. Python 의존성

```bash
pip install -r requirements.txt        # FlagEmbedding 포함 (torch 자동 설치)
```
GPU를 쓸 경우 torch를 CUDA 버전에 맞춰 먼저 설치한 뒤 requirements를 설치하면 된다.
폐쇄망: `pip download -r requirements.txt -d wheels/` 를 온라인에서 수행해 wheels 반입 →
`pip install --no-index --find-links wheels/ -r requirements.txt`

## 3. bge-m3 모델

온라인:
```bash
pip install -U huggingface_hub
huggingface-cli download BAAI/bge-m3 --local-dir /opt/models/bge-m3
```
폐쇄망: 위 명령을 온라인 장비에서 수행 후 `/opt/models/bge-m3` 디렉토리 통째 반입.

환경변수로 지정:
```bash
export EMBED_MODEL=/opt/models/bge-m3   # 미지정 시 "BAAI/bge-m3" (온라인 자동 다운로드)
export EMBED_DEVICE=cpu                 # GPU면 생략 (자동 cuda)
export PG_DSN=postgresql://bohun:bohun@localhost:5432/bohun
```

## 4. DB 초기화 → 시드 → 그래프

```bash
psql "$PG_DSN" -f db/schema.sql
psql "$PG_DSN" -f db/schema_case.sql      # ★ 사건 스키마 신규 컬럼(apply_kind·med_timeline 등) - 안 하면 500
python3 db/seed_codes.py        # 심의체계·주문안 296규칙·KCD 21만행 (수초)
python3 db/build_graph.py       # 지식그래프 재생성 (멱등)
```
※ 시드 CSV가 없거나 원천 엑셀이 갱신됐으면: `python3 db/extract_seed.py <엑셀디렉토리>`

### 4-1. 통계 읽기전용 롤 (권장 - 개인의료정보 2차 방어)

`/stats`(Text-to-SQL)는 앱 단에서 sqlglot로 SQL을 검증하지만, DB 권한에서도 한 번 더 막는다.

```bash
psql "$PG_DSN" -f db/schema_readonly.sql               # bohun_ro 롤 + 통계뷰 SELECT만 부여
psql "$PG_DSN" -c "ALTER ROLE bohun_ro PASSWORD '<실암호>'"
export PG_DSN_RO="postgresql://bohun_ro:<실암호>@localhost:5432/bohun"
```
미설정 시 `stats.py`는 `PG_DSN`으로 폴백한다(개발 편의). **운영에선 반드시 분리** — 앱 검증이
뚫려도 롤 권한이 통계 뷰 밖 테이블(application·medical_record 등) 접근을 차단한다.

## 5. 실측 (핵심 단계)

```bash
python3 mockgen/generate.py     # 저품질 OCR 목코퍼스 24건 + QA 48건 (결정적, seed=42)
python3 scripts/benchmark.py    # ← 실성능 표가 여기서 나온다
```
벤치마크는 {dense, sparse, hybrid} × {오염 원본, verifier 교정} × 오염강도별
hit@1/hit@5와 임베딩 처리량(청크/초)을 출력하고 `scripts/benchmark_result.json`에 저장한다.

체감 데모:
```bash
python3 scripts/demo_chat.py          # 교정본 코퍼스 대상 챗봇
python3 scripts/demo_chat.py --raw    # 오염 원본 대상 (품질 차이 체감)
```

## 6. 결과 해석 기준

샌드박스에서 확보된 기준선(대역 임베더, **sparse 열만 유효**):
sparse 단독 hit@5 = 77%(원본) / 79%(교정). dense·hybrid 수치는 실모델로만 의미가 있다.

bge-m3 연결 후 기대되는 구조 (수치는 예단하지 않는다 — 벤치마크가 답):
- dense는 서브워드 의미 매칭이라 오염(치환·탈락)에 sparse보다 강함 → **"오염 심함" 구간의
  hit@5 개선 폭**이 dense 가치의 직접 지표
- hybrid ≥ max(dense, sparse)가 정상. hybrid가 sparse보다 낮으면 RRF 가중이나
  dense 품질 문제 → config의 RRF_K 조정 검토
- 임베딩 처리량으로 160만 페이지 배치 소요 추정: 페이지당 ~3청크 가정 시
  `480만 청크 ÷ (청크/초)` = 초 단위 소요. GPU/CPU 선택 근거로 사용

## 7. 성능이 기대에 못 미칠 때의 개선 경로 (순서대로)

1. **청킹 상수 조정** (`config/settings.py` CHUNK_MAX_CHARS/OVERLAP) — 비용 0
2. **bge-m3 sparse(lexical weights) 도입** — 현재 sparse는 tsvector(공백 토큰 일치).
   bge-m3 자체 sparse는 서브워드 기반이라 OCR 오염·조사 변이에 더 강함.
   pgvector `sparsevec` 컬럼 추가로 구현 (스키마 변경 필요, 요청 시 구현)
3. **FabriX 문맥 교정 연결** — verifier의 RuleCorrectLLM을 FabrixClient로 교체.
   규칙 교정의 한계(글자 탈락·단어 내 공백 미복원)를 문맥으로 해소. 벤치마크의
   "교정 후" 행이 그대로 효과 측정 지표가 됨
4. **리랭커 추가** (bge-reranker 등) — Top-50 → Top-5 재정렬. 반입 모델 1개 추가

## 8. 로컬 LLM 연결 (Ollama — FabriX 대기 중 생성 기능 활성화)

```bash
brew install ollama && brew services start ollama   # 또는 ollama.com 앱 설치
ollama pull exaone3.5:7.8b     # 한국어 강점 (LG, ~4.8GB). 대안: gemma3:4b, qwen3:8b
export LLM_BACKEND=openai      # 기본 엔드포인트가 Ollama(localhost:11434)로 잡혀 있음
uvicorn api.main:app --port 8000
```
이제 웹 UI 질의에 실제 답변이 생성되고, 검토서 서술부·통계 Text-to-SQL도 동작한다.
Ollama와 FabriX는 같은 OpenAI 호환 경로(FabrixClient)를 쓰므로, 여기서 검증한
프롬프트·흐름이 FabriX 전환 시 그대로 이관된다.

## 9. FabriX 연결 (규격 회신 후)

```bash
export FABRIX_ENDPOINT=...   # core/llm_client.py::FabrixClient._call 주석 참고
export FABRIX_API_KEY=...
export FABRIX_MODEL=...
export LLM_PROVIDER_LABEL=FabriX   # 사용자 안내 문구·상태 배지에 표시될 명칭(개발=Ollama)
```
`LLM_BACKEND=openai` + 위 환경변수가 전부 - 코드 교체 불필요.
`LLM_PROVIDER_LABEL`을 세팅하면 연결 실패 안내·상태 배지가 실제 환경(FabriX)에 맞게 표시된다.
(`scripts/eval_ocr.py`의 `RuleCorrectLLM`만 교정 품질 비교 시 `FabrixClient`로 교체)

## 10. 반입 직후 사전 점검 (권장)

서버를 띄우기 전에 환경을 한 번에 검증한다. DB(RW/RO)·스키마 최신 여부·한글 폰트·생성 LLM
도달성·임베딩 모델 경로를 각각 PASS/FAIL로 출력하고, 하나라도 FAIL이면 종료코드 1.

```bash
python3 scripts/preflight.py
```

폐쇄망 오프라인 참고:
- `EMBED_MODEL`이 로컬 경로면 `embedder.py`가 `HF_HUB_OFFLINE=1`을 자동 설정해 HF 접속 시도를 막는다.
- 의존성은 §2의 `pip download → --no-index` 절차로 반입(신규 `sqlglot` 포함, 순수 파이썬).
- 한글 폰트 미탐색 시 reportlab 내장 CID로 폴백하나, 나눔/노토 반입 시 PDF 가독성이 더 좋다.
