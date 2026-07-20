"""폐쇄망 반입물 일괄 수집 — 인터넷 되는 PC(맥/윈도우/리눅스)에서 실행.

사용법 (레포 루트에서):
    python3 transfer/download_all.py                # 필수: 휠(linux+windows)·bge-m3·폰트·pgvector
    python3 transfer/download_all.py --tools        # + IntelliJ 등 개발도구 설치본(용량 큼)
    python3 transfer/download_all.py --no-models    # 모델 제외 (휠·설치본만 빠르게)
    python3 transfer/download_all.py --wheels-only  # 휠만 재수집 (라이브러리 추가 반입 시)
    python3 transfer/download_all.py --requirements extra.txt --wheels-only   # 추가 패키지 수집

결과: transfer_out/ → 통째로 USB 복사 → 폐쇄망 반입.
      transfer_out/MANIFEST.sha256 로 반입 후 무결성 검증.

대상: Linux x86_64 서버 + Windows x86_64 개발PC, 둘 다 Python 3.12 (아래 상수 수정 가능).
※ 폐쇄망 반입 규정상 .sh 파일 금지 — 이 키트는 전부 .py/.md/데이터 파일로 구성됨.
"""
import argparse
import hashlib
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "transfer_out"
PY_VER = "3.12"
PY_ABI = "cp312"
PY_FULL = "3.12.8"      # 윈도우 설치exe·리눅스 소스 tarball 버전
IDEA_VER = "2024.3.5"
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"   # 기본 PyPI 리눅스 torch는 CUDA 포함 수 GB

FAILS: list[str] = []


def step(msg):
    print(f"\n\033[1;34m── {msg}\033[0m")


def run(cmd: list[str]) -> bool:
    print("$", " ".join(map(str, cmd)))
    if subprocess.run(list(map(str, cmd))).returncode != 0:
        FAILS.append(" ".join(map(str, cmd)))
        return False
    return True


def fetch(url: str, dest: Path) -> bool:
    """urllib 스트리밍 다운로드 (대용량 대응, 이미 있으면 스킵)."""
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  이미 있음: {dest.name}")
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  받는 중: {url}")
    try:
        with urllib.request.urlopen(url) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f, length=1 << 20)
        return True
    except Exception as e:
        print(f"  ⚠ 실패: {e}")
        FAILS.append(url)
        dest.unlink(missing_ok=True)
        return False


def download_wheels(requirements: Path):
    step(f"휠 다운로드 (Python {PY_VER}) — {requirements.name}")
    common = ["--python-version", PY_VER, "--implementation", "cp",
              "--abi", PY_ABI, "--abi", "none", "--abi", "abi3", "--only-binary=:all:"]
    plats = {
        "linux": ["--platform", "manylinux2014_x86_64", "--platform", "manylinux_2_17_x86_64",
                  "--platform", "manylinux_2_28_x86_64"],
        "windows": ["--platform", "win_amd64"],
    }
    for name, plat in plats.items():
        dest = OUT / "wheels" / name
        dest.mkdir(parents=True, exist_ok=True)
        run([sys.executable, "-m", "pip", "download", "-r", requirements, "-d", dest,
             *common, *plat, "--extra-index-url", TORCH_CPU_INDEX])
        # 오프라인 pip 부트스트랩 (venv 안 pip 업그레이드·sdist 대비)
        run([sys.executable, "-m", "pip", "download", "pip", "setuptools", "wheel",
             "-d", dest, *common, *plat])
        # 다운로드된 휠 파일명 목록 = 사실상의 버전 잠금(lock)
        lock = OUT / "wheels" / f"{name}.lock.txt"
        lock.write_text("\n".join(sorted(p.name for p in dest.iterdir())) + "\n", encoding="utf-8")
        print(f"  잠금 기록: {lock}")


def download_models():
    step("bge-m3 모델 (~2.3GB)")
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        run([sys.executable, "-m", "pip", "install", "-q", "huggingface_hub"])
        from huggingface_hub import snapshot_download
    try:
        snapshot_download("BAAI/bge-m3", local_dir=OUT / "models" / "bge-m3")
    except Exception as e:
        print(f"  ⚠ 실패: {e}"); FAILS.append("bge-m3 snapshot_download")

    # 생성 LLM은 내부망 FabriX API 확정 — Ollama·exaone 반입 없음 (환경변수 3개로 연결)


def download_system():
    step("Python 설치본 · pgvector 소스 · 폰트")
    fetch(f"https://www.python.org/ftp/python/{PY_FULL}/python-{PY_FULL}-amd64.exe",
          OUT / "system" / f"python-{PY_FULL}-amd64.exe")
    fetch(f"https://www.python.org/ftp/python/{PY_FULL}/Python-{PY_FULL}.tgz",
          OUT / "system" / f"Python-{PY_FULL}.tgz")
    fetch("https://github.com/pgvector/pgvector/archive/refs/tags/v0.8.0.tar.gz",
          OUT / "system" / "pgvector-0.8.0.tar.gz")
    fetch("https://raw.githubusercontent.com/google/fonts/main/ofl/nanumgothic/NanumGothic-Regular.ttf",
          OUT / "fonts" / "NanumGothic-Regular.ttf")
    print("  ※ PostgreSQL 본체는 서버 OS 확정 후 배포판 패키지로 — TRANSFER_LIST.md 4-나")


def download_tools():
    step("개발 도구 (IntelliJ IDEA CE)")
    fetch(f"https://download.jetbrains.com/idea/ideaIC-{IDEA_VER}.exe",
          OUT / "tools" / f"ideaIC-{IDEA_VER}-windows.exe")
    fetch(f"https://download.jetbrains.com/idea/ideaIC-{IDEA_VER}.tar.gz",
          OUT / "tools" / f"ideaIC-{IDEA_VER}-linux.tar.gz")
    print("  ※ Git for Windows는 https://git-scm.com/download/win 에서 수동 다운로드")


def write_manifest():
    step("무결성 매니페스트 생성")
    lines = []
    for p in sorted(OUT.rglob("*")):
        if p.is_file() and p.name != "MANIFEST.sha256":
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            lines.append(f"{h.hexdigest()}  {p.relative_to(OUT).as_posix()}")
    (OUT / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  {len(lines)}개 파일 기록")


def main():
    ap = argparse.ArgumentParser(description="폐쇄망 반입물 일괄 수집")
    ap.add_argument("--tools", action="store_true", help="IntelliJ 등 개발도구 포함")
    ap.add_argument("--no-models", action="store_true", help="모델(bge-m3) 제외")
    ap.add_argument("--wheels-only", action="store_true", help="휠만 수집 (추가 반입용)")
    ap.add_argument("--requirements", default=str(ROOT / "requirements.txt"),
                    help="수집할 requirements 파일 (기본: 레포 requirements.txt)")
    a = ap.parse_args()

    OUT.mkdir(exist_ok=True)
    download_wheels(Path(a.requirements))
    if not a.wheels_only:
        if not a.no_models:
            download_models()
        download_system()
        if a.tools:
            download_tools()
    write_manifest()

    print("\n" + "=" * 60)
    for d in sorted(OUT.iterdir()):
        if d.is_dir():
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            print(f"  {d.name:12s} {size / 1e9:6.2f} GB")
    if FAILS:
        print(f"\n⚠ 실패 {len(FAILS)}건 — 재실행하거나 TRANSFER_LIST.md 수동 절차로 보완:")
        for f in FAILS:
            print("  -", f)
    else:
        print("\n✅ 전체 수집 완료 → transfer_out/ 을 USB에 복사하세요.")
    print("반입 후 검증: python3 transfer/verify_manifest.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
