# Docker 구성·폐쇄망 반입 가이드

스택: **api**(FastAPI) + **db**(PostgreSQL16+pgvector) + **minio**(원본 객체스토리지) — `docker-compose.yml` 하나로 기동.
LLM은 컨테이너에 넣지 않는다 — 운영은 FabriX(내부 API), 개발만 `--profile llm`(Ollama).

## 1. 온라인 환경(개발 PC)에서 기동

```
docker compose up -d          # api 로컬 빌드(경량: hash 임베딩·mock LLM) + db + minio
docker compose --profile llm up -d   # (선택) Ollama 로컬 LLM 추가
```
- 접속: 화면 http://localhost:8000 · MinIO 콘솔 http://localhost:9001
- 최초 기동 시 엔트리포인트가 스키마 적용→룰·그래프 적재(SEED_ON_START=1)→목데이터(DEMO_SEED=1)까지 자동.

## 2. 폐쇄망 반입 절차

반입물 3종: ① 이미지 tar(아래) ② 코드(bohun_stack 리포) ③ wheel 번들(기존 pva_ai- 반입분 — 폐쇄망 빌드용)

### 2-1. 이미지 내려받기 (외부망)
도커가 있는 PC:
```
python3 scripts/docker_offline.py save -o bundle_docker/
```
→ pgvector/pgvector:pg16, minio, python:3.12-slim 이미지가 tar(+90MB 파트)로 저장되고 SHA256SUMS.txt가 생성된다.
도커가 없는 PC는 skopeo로 데몬 없이 다운로드 가능:
```
skopeo copy docker://pgvector/pgvector:pg16 docker-archive:pgvector_pg16.tar:pgvector/pgvector:pg16
```

### 2-2. 폐쇄망 적재
```
python3 scripts/docker_offline.py load -i bundle_docker/    # 파트 결합·sha 검증·docker load
```

### 2-3. api 이미지 오프라인 빌드
폐쇄망에는 인터넷이 없으므로 pip은 반입 wheel만 사용한다:
```
mkdir wheels && (반입한 bohun_wheels_linux_py312.zip 압축 해제 → wheels/에 복사)
docker compose build --build-arg OFFLINE=1 --build-arg WITH_ML=1 api
docker compose up -d
```

### 2-4. 운영 전환 환경변수 (.env)
```
LLM_BACKEND=openai  FABRIX_ENDPOINT=...  FABRIX_MODEL=...   # FabriX 확정 규격
EMBED_BACKEND=bge   EMBED_MODEL=/models/bge-m3              # 반입 모델 경로 (볼륨 마운트)
MINIO_PUBLIC_ENDPOINT=<서버IP>:9000                          # 브라우저가 접근하는 주소
WITH_ML=1  OFFLINE=1  DEMO_SEED=0
```

## 3. 설계 노트

- **원본 열람**: STORAGE_BACKEND=minio면 `/scan-docs/{id}/file`이 presigned URL로 302 —
  화면 코드는 무수정, `#page=N` 프래그먼트는 리다이렉트에도 유지된다. URL은 만료형(기본 600초)이라 DB에 저장하지 않는다.
- **LangGraph**: ORCH_BACKEND=langgraph(compose 기본). 미반입 환경은 plain 자동 폴백 — 결과 동등성은 tests/test_orchestration.py가 보증.
- **GPU 서빙**: FabriX 외 자체 vLLM 컨테이너를 쓰려면 NVIDIA Container Toolkit 반입 필요(별도 결정).
- **보안정책**: 기관 컨테이너 운용 허가 선행 확인 — Podman 요구 시 compose 호환(podman-compose)으로 동일 구성.
