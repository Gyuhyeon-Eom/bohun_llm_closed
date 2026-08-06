# -*- coding: utf-8 -*-
"""도커 이미지 오프라인 반입 도구 — save/분할/검증/load 를 한 곳에서 (폐쇄망 .sh 금지 대응).

외부망(도커 有):  python3 scripts/docker_offline.py save -o bundle_docker/
  → 스택 이미지 전체를 tar로 저장 후 90MB 파트 분할 + SHA256SUMS.txt 생성
폐쇄망(도커 有):  python3 scripts/docker_offline.py load -i bundle_docker/
  → 파트 결합·sha256 검증 후 docker load

도커 없이 이미지 자체를 내려받는 경우(skopeo): docs/DOCKER.md 참조.
"""
import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

# 스택 구성 이미지 (compose와 일치 — 갱신 시 여기와 docker-compose.yml 동시 수정)
IMAGES = [
    "pgvector/pgvector:pg16",
    "minio/minio:RELEASE.2025-09-07T16-13-09Z",
    "python:3.12-slim",            # api 이미지의 베이스 (폐쇄망 빌드용)
]
PART = 90 * 1024 * 1024


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def save(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    sums = []
    for img in IMAGES:
        name = img.replace("/", "_").replace(":", "_") + ".tar"
        tar = out / name
        print(f"[save] {img} → {name}")
        subprocess.run(["docker", "pull", img], check=True)
        subprocess.run(["docker", "save", "-o", str(tar), img], check=True)
        sums.append(f"{sha256(tar)}  {name}")
        # 90MB 분할 (GitHub 반입 경로용 — USB 직반입이면 파트 무시하고 tar만 써도 됨)
        data = tar.read_bytes()
        if len(data) > PART:
            for i in range(0, len(data), PART):
                (out / f"{name}.part{i // PART:02d}").write_bytes(data[i:i + PART])
            tar.unlink()
            print(f"       분할 {-(-len(data) // PART)}파트 (원본 tar 제거)")
    (out / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8")
    print(f"[save] 완료 — {out}/ 를 반입 매체로")


def load(indir: Path):
    sums = {line.split()[1]: line.split()[0]
            for line in (indir / "SHA256SUMS.txt").read_text().splitlines() if line.strip()}
    for name, want in sums.items():
        tar = indir / name
        parts = sorted(indir.glob(f"{name}.part*"))
        if parts:  # 파트 결합
            print(f"[load] {name} 파트 {len(parts)}개 결합")
            with open(tar, "wb") as f:
                for p in parts:
                    f.write(p.read_bytes())
        got = sha256(tar)
        if got != want:
            print(f"[load] 무결성 불일치: {name}\n  기대 {want}\n  실측 {got}", file=sys.stderr)
            sys.exit(2)
        print(f"[load] docker load < {name}")
        subprocess.run(["docker", "load", "-i", str(tar)], check=True)
    print("[load] 완료 — docker compose up -d")


def main():
    ap = argparse.ArgumentParser(description="도커 이미지 오프라인 반입")
    ap.add_argument("mode", choices=["save", "load"])
    ap.add_argument("-o", "--out", default="bundle_docker")
    ap.add_argument("-i", "--indir", default="bundle_docker")
    a = ap.parse_args()
    save(Path(a.out)) if a.mode == "save" else load(Path(a.indir))


if __name__ == "__main__":
    main()
