"""폐쇄망 리눅스 서버 오프라인 설치 — USB 반입물(transfer_out/)로 전체 구성.

전제:
  - 이 레포지토리 폴더가 서버에 복사되어 있음
  - transfer_out/ 이 레포 루트에 복사되어 있음 (USB에서)
  - Python 3.12, PostgreSQL 16+ 는 OS 패키지로 선설치 (TRANSFER_LIST.md 4 참고)

사용법 (레포 루트에서):  python3 transfer/install_server.py
재실행 안전(멱등). root 권한이 필요한 단계(폰트 설치)는 sudo를 시도하고,
실패하면 수동 명령을 출력한다.

※ 폐쇄망 반입 규정상 .sh 파일 금지 — 설치 로직 전체를 이 .py 하나로 처리.
"""
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IN = ROOT / "transfer_out"
VENV = ROOT / ".venv"
PIP = VENV / "bin" / "pip"
PY = VENV / "bin" / "python"
PG_DSN = os.environ.get("PG_DSN", "postgresql://bohun:bohun@localhost:5432/bohun")
OFFLINE_ENV = {**os.environ, "PG_DSN": PG_DSN, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}


def step(msg):
    print(f"\n\033[1;34m── {msg}\033[0m")


def die(msg):
    print(f"✖ {msg}"); sys.exit(1)


def run(cmd, env=None, check=True) -> bool:
    print("$", " ".join(map(str, cmd)))
    ok = subprocess.run(list(map(str, cmd)), env=env).returncode == 0
    if check and not ok:
        die(f"명령 실패: {' '.join(map(str, cmd))}")
    return ok


def sudo_or_hint(cmd, hint):
    """sudo 시도, 안 되면 수동 명령 안내만 하고 계속 진행."""
    if not run(["sudo", *cmd], check=False):
        print(f"  ⚠ 권한 부족 — 수동 실행 필요: sudo {hint}")


def verify_manifest():
    step("0/6 반입물 무결성 검증 (MANIFEST.sha256)")
    mf = IN / "MANIFEST.sha256"
    if not mf.exists():
        die("transfer_out/MANIFEST.sha256 없음 — USB 반입물을 레포 루트에 복사하세요")
    bad = 0
    for line in mf.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, rel = line.split(None, 1)
        p = IN / rel.strip()
        h = hashlib.sha256()
        if not p.exists():
            print(f"  누락: {rel}"); bad += 1; continue
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != digest:
            print(f"  불일치: {rel}"); bad += 1
    if bad:
        die(f"체크섬 불일치/누락 {bad}건 — USB 복사 상태 확인")
    print("  OK")


def install_wheels():
    step("1/6 Python 라이브러리 (오프라인)")
    if not VENV.exists():
        run([sys.executable, "-m", "venv", VENV])
    links = IN / "wheels" / "linux"
    run([PIP, "install", "--no-index", "--find-links", links, "--upgrade", "pip", "setuptools", "wheel"])
    run([PIP, "install", "--no-index", "--find-links", links, "-r", ROOT / "requirements.txt"])
    print(f"  버전 잠금 기준: {IN / 'wheels' / 'linux.lock.txt'}")


def place_models():
    step("2/6 임베딩 모델 (bge-m3)")
    src, dst = IN / "models" / "bge-m3", ROOT / "models" / "bge-m3"
    if src.is_dir():
        if not dst.exists():
            shutil.copytree(src, dst)
        print(f"  배치 완료: {dst}")
    else:
        print("  ⚠ 반입물에 모델 없음 — EMBED_BACKEND=hash 로만 동작")


def check_fabrix():
    step("3/6 생성 LLM — 내부망 FabriX API (반입물 없음)")
    ep = os.environ.get("FABRIX_ENDPOINT")
    if ep:
        print(f"  FABRIX_ENDPOINT={ep}")
    else:
        print("  ⚠ FABRIX_ENDPOINT 미설정 — 기동 전 환경변수 3개(FABRIX_ENDPOINT/API_KEY/MODEL)를")
        print("    발급받은 FabriX 규격으로 설정하세요. 설정 전에는 챗봇에 'LLM 미연결' 안내가 뜹니다.")


def install_font():
    step("4/6 나눔고딕 폰트 (의결서 PDF)")
    ttf = IN / "fonts" / "NanumGothic-Regular.ttf"
    if ttf.exists():
        dst = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"
        sudo_or_hint(["mkdir", "-p", os.path.dirname(dst)], f"mkdir -p {os.path.dirname(dst)}")
        sudo_or_hint(["cp", "-n", ttf, dst], f"cp {ttf} {dst}")
        if shutil.which("fc-cache"):
            sudo_or_hint(["fc-cache", "-f"], "fc-cache -f")


def init_db():
    step(f"5/6 DB 초기화 (PG_DSN={PG_DSN})")
    if subprocess.run(["psql", PG_DSN, "-c", "SELECT 1"], capture_output=True).returncode != 0:
        print("  ⚠ PostgreSQL 연결 실패 — pgvector 포함 설치 후 재실행 (TRANSFER_LIST.md 4)")
        return
    run(["psql", PG_DSN, "-c", "CREATE EXTENSION IF NOT EXISTS vector"])
    run(["psql", PG_DSN, "-q", "-f", ROOT / "db" / "schema.sql"])
    run(["psql", PG_DSN, "-q", "-f", ROOT / "db" / "schema_case.sql"])
    env = dict(OFFLINE_ENV)
    if (ROOT / "models" / "bge-m3").is_dir():
        env["EMBED_BACKEND"] = "bge"
        env["EMBED_MODEL"] = str(ROOT / "models" / "bge-m3")
    else:
        env["EMBED_BACKEND"] = "hash"
    run([PY, ROOT / "db" / "seed_codes.py"], env=env)
    run([PY, ROOT / "mockgen" / "generate_cases.py"], env=env)
    run([PY, ROOT / "db" / "build_graph.py"], env=env)
    run([PY, ROOT / "scripts" / "ingest_grade_criteria.py"], env=env, check=False)


def smoke():
    step("6/6 스모크 테스트")
    run([PY, ROOT / "tests" / "test_smoke.py"])


if __name__ == "__main__":
    if not IN.is_dir():
        die("transfer_out/ 이 없습니다 — USB 반입물을 레포 루트에 복사하세요")
    verify_manifest()
    install_wheels()
    place_models()
    check_fabrix()
    install_font()
    init_db()
    smoke()
    print("""
============================================================
설치 완료. 서버 기동:
  python3 start.py            # 오프라인 env 자동 설정 + uvicorn 기동
  → http://127.0.0.1:8000
============================================================""")
