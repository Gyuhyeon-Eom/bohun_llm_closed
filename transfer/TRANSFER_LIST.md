# TRANSFER_LIST — USB 반입 체크리스트

> 자동 수집 대신 **손으로 하나씩 챙길 때의 다운로드 링크 목록**은 [PACKING_LIST.md](PACKING_LIST.md) 참고.

폐쇄망 구축에 필요한 전체 반입물 목록. **1~5는 `python3 transfer/download_all.py` 한 번으로 자동 수집**되어
`transfer_out/`에 모이고, 6~7은 수동 항목이다. 반입 후 `python3 transfer/verify_manifest.py`로 무결성 검증.

> **반입 규정: .sh 파일 금지.** 이 키트의 수집·설치·기동 도구는 전부 `.py`이며,
> 반입되는 리포지토리에는 셸 스크립트가 없다. USB에는 아래 항목을 **일반 파일/폴더 그대로** 담는다.

| # | 항목 | 수집 방법 | 용도 | 대상 | 용량(약) |
|---|---|---|---|---|---|
| 1 | **이 레포지토리 폴더** | 폴더 통째 복사 (이력 필요 시 `.git` 포함 — 반입 규정 확인 후) | 앱 코드 | 서버·개발PC | ~10MB |
| 2-가 | `wheels/linux/` | download_all.py 자동 | Python 라이브러리 (torch CPU 포함) | 리눅스 서버 | ~1GB |
| 2-나 | `wheels/windows/` | download_all.py 자동 | Python 라이브러리 | 윈도우 개발PC | ~1GB |
| 3-가 | `models/bge-m3/` | download_all.py 자동 | 임베딩 모델 | 서버 | 2.3GB |
| 3-나 | `ollama/` (본체 tgz·exe + exaone 블롭) | download_all.py 자동 (모델 블롭은 온라인 PC에 ollama 필요¹) | 생성 LLM | 서버·개발PC | ~5.5GB |
| 4-가 | `system/python-3.12.8-amd64.exe` | download_all.py 자동 | Python 본체 | 윈도우 개발PC | 26MB |
| 4-나 | **PostgreSQL 16+ 패키지** | 수동² — 서버 OS 확정 후 배포판 패키지 | DB | 서버 | ~60MB |
| 4-다 | `system/pgvector-0.8.0.tar.gz` | download_all.py 자동 (소스 빌드: make) | 벡터 확장 | 서버 | 1MB |
| 4-라 | `system/Python-3.12.8.tgz` | download_all.py 자동 (OS 패키지 없을 때 소스 빌드용) | Python 본체 | 서버 | 27MB |
| 5 | `fonts/NanumGothic-Regular.ttf` | download_all.py 자동 | 의결서 PDF 한글 | 서버 | 4MB |
| 6 | IntelliJ IDEA CE 설치본 (win/linux) | `download_all.py --tools` | IDE | 개발PC | ~1GB×2 |
| 7 | Git for Windows | 수동 — https://git-scm.com/download/win | 형상관리 | 윈도우 개발PC | 60MB |

**총 용량: 기본 ~10GB / --tools 포함 ~12GB → 32GB USB 권장**

---

¹ **exaone 모델 블롭 수동 수집** (온라인 PC에 ollama 없을 때):
```
# 온라인 PC에서 (맥 기준)
brew install ollama && brew services start ollama    # 또는 ollama.com 설치
ollama pull exaone3.5:7.8b
# ~/.ollama/models 디렉토리를 transfer_out/ollama/models 로 통째 복사
```

² **PostgreSQL + 빌드도구 오프라인 패키지 수집** (서버 OS 확정 후 택1).
⚠ 반드시 **의존 패키지까지 전부** 받아야 한다 — 내부망에서는 부족분을 추가로 받을 방법이 없다:
```
# Ubuntu 24.04 — 인터넷 되는 "동일 버전·깨끗한" 우분투(컨테이너 권장)에서
# ★ python3 계열 포함 — 서버에 파이썬이 아예 없어도 이 묶음으로 설치된다 (24.04의 python3 = 3.12)
apt-get update
apt-get install --download-only -o Dir::Cache::archives=$PWD/pgpkgs -y \
    python3 python3-venv python3-pip \
    postgresql-16 postgresql-client-16 postgresql-server-dev-16 build-essential
# → pgpkgs/*.deb 전체(의존성 포함)를 transfer_out/system/pgpkgs/ 로 복사
#   내부망 설치: sudo dpkg -i transfer_out/system/pgpkgs/*.deb   (dpkg는 OS 내장 — 파이썬 불필요)

# RHEL 8/9 계열 (--resolve 가 의존성 포함)
dnf download --resolve --alldeps --destdir ./pgpkgs \
    python3.12 python3.12-pip \
    postgresql16-server postgresql16-devel gcc make redhat-rpm-config
#   내부망 설치: sudo dnf install --disablerepo='*' ./pgpkgs/*.rpm   (dnf/rpm은 OS 내장)
```
⚠ 서버 OS의 python3 버전이 휠 수집 기준(PY_VER=3.12)과 일치해야 한다 — Ubuntu 22.04는
기본이 3.10이므로, 22.04 서버라면 download_all.py의 PY_VER를 3.10으로 바꿔 휠을 재수집할 것.
pgvector는 4-다 소스를 내부망에서 `make && sudo make install` (위 빌드도구·server-dev 필요).
Python 3.12도 OS 패키지가 없으면 같은 방식으로 패키지 묶음을 수집(4-라 소스 빌드는 최후 수단 — libssl-dev 등 빌드 의존이 더 필요).

---

## 반입 후 설치 순서

### 리눅스 서버 — "파이썬이 하나도 없는" 포맷 상태 기준

**0단계(부트스트랩)는 파이썬 없이 OS 내장 도구만 쓴다** — .py 스크립트는 파이썬이 생긴 뒤의 이야기:

1. 레포 폴더 + `transfer_out/`을 서버에 복사 (파일 복사 — 도구 불필요)
2. **OS 패키지 설치 (파이썬 포함)**: `sudo dpkg -i transfer_out/system/pgpkgs/*.deb`
   — dpkg(데비안)/rpm·dnf(레드햇)는 OS에 기본 내장이라 파이썬이 없어도 실행된다.
   이 묶음에 python3·pip·venv·PostgreSQL·빌드도구가 전부 들어 있다 (위 ② 수집 명령 참고)
3. `python3 --version`으로 파이썬 생겼는지 확인, pgvector 소스 `make && sudo make install`, DB 계정 생성
4. **이제부터 .py 사용 가능**: `python3 transfer/install_server.py` — 무결성 검증→휠→모델→폰트→DB시드→스모크 자동
5. `python3 start.py` → http://127.0.0.1:8000

### 윈도우 개발 PC
→ [INSTALL_WINDOWS.md](INSTALL_WINDOWS.md) — 파이썬 없이 시작해도 됨:
`system\python-3.12.8-amd64.exe` 더블클릭 설치가 0단계 (설치 프로그램은 파이썬 불필요)

## 내부망(폐쇄망)에서는 어떤 다운로드도 일어나지 않는다

다운로드는 **전부 외부망에서 download_all.py가 수행**하고, 내부망의 설치·기동 단계는 반입 파일만 사용한다:

| 내부망 단계 | 사용하는 반입물 | 네트워크 |
|---|---|---|
| `verify_manifest.py` | MANIFEST.sha256 | 없음 (해시 계산만) |
| `install_server.py` 휠 설치 | `wheels/linux/` (`pip --no-index --find-links`) | 없음 (PyPI 접속 차단됨) |
| 모델·폰트·Ollama 배치 | `models/` `fonts/` `ollama/` 파일 복사 | 없음 |
| DB 스키마·시드 | 레포 내 sql·py + 로컬 PostgreSQL | 없음 (localhost DB만) |
| `start.py` 기동 | `.venv` + 로컬 모델 | 없음 — `HF_HUB_OFFLINE=1` `TRANSFORMERS_OFFLINE=1` 자동 설정으로 허브 접속 시도 자체를 차단 |

내부망에서 무언가 부족하면 그 자리에서 받을 수 없으므로, 반입 전 **외부망에서 리허설**을 권장:
인터넷 차단한 VM(또는 네트워크 끊은 PC)에 transfer_out/만 넣고 `install_server.py`가 끝까지 도는지 확인.

## 주의

- **휠은 플랫폼·Python 버전에 묶인다** — 서버 Python이 3.12가 아니면 download_all.py의 `PY_VER` 수정 후 재수집
- 폐쇄망 서버에서는 항상 `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1` (start.py·install_server.py가 자동 설정)
- `wheels/*.lock.txt`가 반입 시점의 버전 잠금 — 이후 라이브러리 추가 반입은
  `python3 transfer/download_all.py --wheels-only --requirements 추가목록.txt` 로 같은 방식 수집
- PPP존 연계 기능(유사사례 원문 등 통합보훈시스템 조회)은 **내부 API 호출로 처리 예정 — 별도 반입물 없음**
