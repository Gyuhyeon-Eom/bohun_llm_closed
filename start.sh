#!/bin/bash
# 보훈심사 AI 서비스 기동 스크립트 (경로 하드코딩 없음 — 레포 위치 기준 동작)
set -euo pipefail
cd "$(dirname "$0")"

# PostgreSQL 클라이언트 (Homebrew keg-only 대응, 이미 PATH에 있으면 무해)
command -v brew >/dev/null 2>&1 && PATH="$(brew --prefix postgresql@17 2>/dev/null)/bin:$PATH"

export PG_DSN="${PG_DSN:-postgresql://bohun:bohun@localhost:5432/bohun}"

# 임베딩: models/bge-m3 반입돼 있으면 실모델, 없으면 hash 대역
if [ -d "$PWD/models/bge-m3" ]; then
  export EMBED_BACKEND="${EMBED_BACKEND:-bge}"
  export EMBED_MODEL="${EMBED_MODEL:-$PWD/models/bge-m3}"
  export EMBED_DEVICE="${EMBED_DEVICE:-cpu}"
else
  export EMBED_BACKEND="${EMBED_BACKEND:-hash}"
fi

# LLM: Ollama (OpenAI 호환 경로) — ollama 미기동 시 화면에 미연결 안내가 뜬다
export LLM_BACKEND="${LLM_BACKEND:-openai}"
export FABRIX_ENDPOINT="${FABRIX_ENDPOINT:-http://localhost:11434/v1/chat/completions}"
export FABRIX_API_KEY="${FABRIX_API_KEY:-ollama}"
export FABRIX_MODEL="${FABRIX_MODEL:-exaone3.5:7.8b}"

exec ./.venv/bin/uvicorn api.main:app --host 127.0.0.1 --port "${PORT:-8000}"
