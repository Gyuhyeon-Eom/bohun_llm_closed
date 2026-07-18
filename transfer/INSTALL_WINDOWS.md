# INSTALL_WINDOWS — 폐쇄망 윈도우 개발 PC 세팅

반입물(`transfer_out/`) 기준. 모든 명령은 PowerShell. (.sh 없이 .py·설치본만 사용)

## 1. 기본 도구 설치

1. **Python 3.12**: `transfer_out\system\python-3.12.8-amd64.exe` 실행
   - ✅ "Add python.exe to PATH" 체크 후 Install Now
2. **Git**: Git for Windows 설치본 실행 (TRANSFER_LIST 7)
3. **IntelliJ IDEA CE**: `transfer_out\tools\ideaIC-*-windows.exe` 실행 (--tools로 수집한 경우)

## 2. 레포지토리 복사

USB의 레포 폴더를 작업 위치로 복사 (예: `C:\work\bohun_llm_closed`) 후 그 폴더에서 진행.

```powershell
cd C:\work\bohun_llm_closed
python transfer\verify_manifest.py ..\transfer_out   # 반입물 무결성 확인 (경로는 실제 위치로)
```

## 3. Python 가상환경 + 라이브러리 (오프라인)

```powershell
python -m venv .venv
.\.venv\Scripts\pip install --no-index --find-links ..\transfer_out\wheels\windows --upgrade pip setuptools wheel
.\.venv\Scripts\pip install --no-index --find-links ..\transfer_out\wheels\windows -r requirements.txt
```

## 4. IntelliJ 설정 (1회)

1. **Python 플러그인 (오프라인)**: 온라인 PC에서 https://plugins.jetbrains.com 의
   "Python Community Edition" zip을 받아 반입 → `Settings → Plugins → ⚙ → Install Plugin from Disk` → 재시작
2. **프로젝트 열기**: `File → Open` 으로 레포 폴더 선택
3. **인터프리터(SDK)**: `File → Project Structure → SDKs → + → Add Python SDK
   → Existing environment → <레포>\.venv\Scripts\python.exe` 선택 후 프로젝트 SDK로 지정
4. **실행 구성** (`Run → Edit Configurations → + → Python`):
   - "Module name" 모드 선택 → `uvicorn`
   - Parameters: `api.main:app --port 8000`
   - Working directory: 레포 루트
   - Environment variables: `EMBED_BACKEND=hash;LLM_BACKEND=mock;HF_HUB_OFFLINE=1;TRANSFORMERS_OFFLINE=1`

## 5. 실행

```powershell
python start.py        # 오프라인 env 자동 설정 (모델 없으면 hash, DB는 PG_DSN 환경변수)
# http://127.0.0.1:8000
```

DB까지 붙일 때: 서버의 PostgreSQL을 바라보도록 `$env:PG_DSN` 설정 (또는 윈도우용 PostgreSQL 설치본을 별도 반입).

## 확인

```powershell
.\.venv\Scripts\python tests\test_smoke.py    # PASS 3건이면 정상
```
