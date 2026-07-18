"""반입물 무결성 단독 검증 — USB 복사 직후 아무 PC에서나 실행 (윈도우 포함).

사용법: python3 transfer/verify_manifest.py [transfer_out 경로]
"""
import hashlib
import sys
from pathlib import Path

base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "transfer_out"
mf = base / "MANIFEST.sha256"
if not mf.exists():
    sys.exit(f"✖ {mf} 없음")

bad = ok = 0
for line in mf.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    digest, rel = line.split(None, 1)
    p = base / rel.strip()
    if not p.exists():
        print(f"누락:   {rel}"); bad += 1; continue
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() == digest:
        ok += 1
    else:
        print(f"불일치: {rel}"); bad += 1

print(f"\n검증 {ok + bad}건 — 정상 {ok} / 문제 {bad}")
sys.exit(1 if bad else 0)
