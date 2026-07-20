# PACKING_LIST — 온라인에서 미리 챙겨 가는 준비물 (링크 목록)

원칙: **다운로드·수집은 전부 온라인에서, 내부망에서는 "설치 프로그램 실행 + 폴더 복사"만.**
라이브러리·모델은 설치가 아니라 "들고 간 파일을 풀어놓는" 수준으로 끝난다.

## 0. 내부망 API로 해결 — 반입 안 하는 것들 (확정)

| 항목 | 처리 |
|---|---|
| **생성 LLM** | **FabriX(OpenAI 호환 API) 확정 — exaone·Ollama 반입 없음.** 기동 전 환경변수 3개만 설정: `FABRIX_ENDPOINT` `FABRIX_API_KEY` `FABRIX_MODEL` (발급 규격) |
| **PPP존 데이터** (유사사례 원문·통합보훈시스템 조회) | 내부 API 연계 — 반입물 없음 |
| **임베딩(bge-m3)** | 현재는 로컬 모델 반입이 기본. FabriX 임베딩 API 제공 확인 시 대체 가능(어댑터 추가 필요) |

> 자동 수집(`python3 transfer/download_all.py`)이 아래 대부분을 대신해 주지만,
> 이 문서는 **손으로 하나씩 챙길 때의 링크 목록**이다. 체크박스로 쓰면 된다.

---

## 1. 공통 (서버·개발 PC 둘 다 필요)

| ✔ | 항목 | 받는 곳 | 크기 |
|---|---|---|---|
| ☐ | **이 레포지토리** | https://github.com/Gyuhyeon-Eom/bohun_llm_closed → `Code → Download ZIP` | ~10MB |
| ☐ | **파이썬 라이브러리 휠 2벌** (리눅스용·윈도우용) | 링크 없음 — 아래 "휠은 왜 링크가 없나" 참고, 명령 2줄로 수집 | 각 ~1GB |
| ☐ | **bge-m3 임베딩 모델** | https://huggingface.co/BAAI/bge-m3 → `Files and versions` 탭에서 전체 파일 (또는 `hf download BAAI/bge-m3 --local-dir bge-m3`) | 2.3GB |
| ☐ | **나눔고딕 폰트** (PDF 산출용) | https://raw.githubusercontent.com/google/fonts/main/ofl/nanumgothic/NanumGothic-Regular.ttf | 4MB |
| ☐ | **매뉴얼·법령 스캔본 PDF** (내부 자료) | 보유분 → USB의 `data/originals/`용 | — |

### 휠(라이브러리)은 왜 링크 목록이 없나
`requirements.txt`는 이름 목록일 뿐 파일이 아니고, 실제로는 **의존성까지 40~60개 파일**이
플랫폼별로 다르게 필요하다. 링크를 손으로 모으는 건 비현실적이라, pip의 다운로드 기능이
"링크 수집기" 역할을 한다 (설치 아님 — 파일만 받아 폴더에 모음):

```
pip download -r requirements.txt -d wheels/linux   --python-version 3.12 --implementation cp --abi cp312 --abi none --abi abi3 --only-binary=:all: --platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 --platform manylinux_2_28_x86_64 --extra-index-url https://download.pytorch.org/whl/cpu
pip download -r requirements.txt -d wheels/windows --python-version 3.12 --implementation cp --abi cp312 --abi none --abi abi3 --only-binary=:all: --platform win_amd64 --extra-index-url https://download.pytorch.org/whl/cpu
```
(= `python3 transfer/download_all.py --wheels-only` 한 줄과 동일)

---

## 2. 우분투 서버용

| ✔ | 항목 | 받는 곳 | 크기 |
|---|---|---|---|
| ☐ | **OS 패키지 묶음** (python3·pip·venv·PostgreSQL16·빌드도구·tesseract OCR, 의존성 포함 .deb 전체) | 링크 다발이 아니라 명령 1줄 — 서버와 **같은 버전** 우분투에서: `apt-get install --download-only -o Dir::Cache::archives=$PWD/pgpkgs -y python3 python3-venv python3-pip postgresql-16 postgresql-client-16 postgresql-server-dev-16 build-essential tesseract-ocr tesseract-ocr-kor` | ~230MB |
| ☐ | **pgvector 소스** | https://github.com/pgvector/pgvector/archive/refs/tags/v0.8.0.tar.gz | 1MB |
| ☐ | (예비) Python 3.12 소스 | https://www.python.org/ftp/python/3.12.8/Python-3.12.8.tgz | 27MB |

**내부망 서버에서 하는 일 (전부 로컬 파일, 다운로드 0):**
```
sudo dpkg -i pgpkgs/*.deb                    # ① 프로그램 설치 (파이썬·PG — OS 내장 dpkg 사용)
cd pgvector-0.8.0 && make && sudo make install   # ② pgvector 빌드 (1분)
python3 transfer/install_server.py           # ③ 나머지 전자동: 휠 설치·모델 배치·DB 시드
```

---

## 3. 윈도우 개발 PC용

| ✔ | 항목 | 받는 곳 | 크기 |
|---|---|---|---|
| ☐ | **Python 3.12 설치 프로그램** | https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe | 26MB |
| ☐ | **Git for Windows** | https://git-scm.com/download/win (Standalone Installer 64-bit) | 60MB |
| ☐ | **IntelliJ IDEA CE** | https://download.jetbrains.com/idea/ideaIC-2024.3.5.exe | ~800MB |
| ☐ | **IntelliJ Python 플러그인 zip** (오프라인 설치용) | https://plugins.jetbrains.com/plugin/7322-python-community-edition → Versions에서 IDE 버전에 맞는 zip | ~100MB |
| ☐ | (선택) **PostgreSQL 윈도우 설치본** (개발 PC에 DB 직접 둘 경우) | https://www.enterprisedb.com/downloads/postgres-postgresql-downloads → Windows x86-64 16.x | ~350MB |
| ☐ | **Tesseract OCR 윈도우 설치본** (스캔 의무기록 OCR — 설치 시 "Additional language data"에서 Korean 체크) | https://github.com/UB-Mannheim/tesseract/wiki → tesseract-ocr-w64-setup 최신 | ~80MB |

**내부망 개발 PC에서 하는 일 (설치 프로그램 더블클릭 + 명령 1줄):**
```
① python-3.12.8-amd64.exe 실행 ("Add python.exe to PATH" 체크)
② Git·IntelliJ 설치 프로그램 실행
③ 라이브러리는 설치가 아니라 들고 간 휠을 풀어놓기:
   python -m venv .venv
   .venv\Scripts\pip install --no-index --find-links ..\wheels\windows -r requirements.txt
```

---

## USB 구성 예시 (총 ~6GB, 16GB면 충분)

```
USB/
├─ bohun_llm_closed/        ← 레포 ZIP 풀어둔 것
├─ wheels/linux/  wheels/windows/
├─ models/bge-m3/
├─ pgpkgs/ (*.deb)          ├─ pgvector-0.8.0.tar.gz
├─ installers/ (python·git·intellij exe들)
├─ fonts/NanumGothic-Regular.ttf
└─ originals/ (매뉴얼·법령 스캔본 PDF)
```

## 주의 (버전 궁합 — 이거만 지키면 안 걸림)

1. **휠 ↔ 파이썬 버전**: 휠은 Python 3.12 기준으로 받았으니 서버·개발 PC 모두 3.12를 설치할 것.
   서버가 Ubuntu 22.04(기본 3.10)라면 휠을 3.10 기준으로 다시 수집 (`download_all.py`의 PY_VER 수정)
2. **OS 패키지 묶음 ↔ 서버 우분투 버전**: 반드시 같은 버전 우분투(컨테이너 추천)에서 수집
3. 수집이 끝나면 `python3 transfer/verify_manifest.py`용 매니페스트를 만들어 두면
   USB 복사 오류를 반입 후 즉시 잡을 수 있다 (`download_all.py`는 자동 생성)
