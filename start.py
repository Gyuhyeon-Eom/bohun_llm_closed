"""보훈심사 AI 서버 기동 (폐쇄망용 — .sh 반입 금지라 .py로 제공).

사용법: python3 start.py          # 리눅스 서버·윈도우 공통
환경변수로 재정의 가능: PG_DSN, EMBED_*, LLM_BACKEND, FABRIX_*, PORT
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

env = os.environ
env.setdefault("PG_DSN", "postgresql://bohun:bohun@localhost:5432/bohun")

# 폐쇄망 필수: 로컬 모델이어도 허브 접속을 시도하지 않도록 차단
env.setdefault("HF_HUB_OFFLINE", "1")
env.setdefault("TRANSFORMERS_OFFLINE", "1")

# 임베딩: models/bge-m3 반입돼 있으면 실모델, 없으면 hash 대역
if (ROOT / "models" / "bge-m3").is_dir():
    env.setdefault("EMBED_BACKEND", "bge")
    env.setdefault("EMBED_MODEL", str(ROOT / "models" / "bge-m3"))
    env.setdefault("EMBED_DEVICE", "cpu")
else:
    env.setdefault("EMBED_BACKEND", "hash")

# LLM: Ollama (OpenAI 호환 경로) — 미기동 시 화면에 미연결 안내가 뜬다
env.setdefault("LLM_BACKEND", "openai")
env.setdefault("FABRIX_ENDPOINT", "http://localhost:11434/v1/chat/completions")
env.setdefault("FABRIX_API_KEY", "ollama")
env.setdefault("FABRIX_MODEL", "exaone3.5:7.8b")

venv_bin = ROOT / ".venv" / ("Scripts" if os.name == "nt" else "bin")
uvicorn = venv_bin / ("uvicorn.exe" if os.name == "nt" else "uvicorn")
if not uvicorn.exists():
    sys.exit("✖ .venv 없음 — 먼저 transfer/install_server.py (서버) 또는 INSTALL_WINDOWS.md (개발PC) 절차 수행")

cmd = [str(uvicorn), "api.main:app", "--host", "127.0.0.1", "--port", env.get("PORT", "8000")]
print("$", " ".join(cmd))
sys.exit(subprocess.run(cmd, env=env).returncode)
