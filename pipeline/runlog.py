# -*- coding: utf-8 -*-
"""파이프라인 단계 표출 계층 — 콘솔(사람용) + JSONL 매니페스트(감사·재현용).

모든 플로우는 RunLog를 통해서만 진행 상황을 표출한다:
  - 콘솔: [HH:MM:SS] ▶단계 시작 / ✔단계 완료(소요·핵심 수치) / ⚠경고
  - out/runs/{run_id}.jsonl: 단계별 {stage, status, elapsed_s, detail} 1행씩 —
    "어느 입력이 어느 단계를 거쳐 무엇이 됐는지"를 파일로 남긴다 (폐쇄망 감사 추적).
"""
import json
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


class RunLog:
    def __init__(self, flow: str, out_dir: str = "out"):
        self.run_id = f"{flow}_{datetime.now().strftime('%y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        self.path = Path(out_dir) / "runs" / f"{self.run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.flow = flow
        self._t0 = time.time()
        # 프롬프트 버전 지문(260730) — 이 런이 어떤 프롬프트로 생성했는지 매니페스트에 고정
        try:
            from core.llm_client import prompt_fingerprints
            fps = prompt_fingerprints()
        except Exception:
            fps = {}
        self._write({"stage": "_start", "flow": flow, "prompt_fingerprints": fps})

    def _write(self, rec: dict):
        rec = {"ts": datetime.now().isoformat(timespec="seconds"), **rec}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    def _echo(self, msg: str):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

    def info(self, msg: str, **detail):
        self._echo(f"  {msg}")
        self._write({"stage": "_info", "msg": msg, **detail})

    def warn(self, msg: str, **detail):
        self._echo(f"  ⚠ {msg}")
        self._write({"stage": "_warn", "msg": msg, **detail})

    @contextmanager
    def stage(self, name: str, **inputs):
        """단계 컨텍스트 — 시작·완료·실패를 자동 표출. detail(dict)을 yield 받아 채우면 기록됨."""
        self._echo(f"▶ {name}" + (f"  {inputs}" if inputs else ""))
        t0, detail = time.time(), {}
        try:
            yield detail
        except Exception as e:
            self._write({"stage": name, "status": "error", "elapsed_s": round(time.time() - t0, 1),
                         "error": str(e), **inputs})
            self._echo(f"✘ {name} 실패 — {e}")
            raise
        el = round(time.time() - t0, 1)
        summary = " ".join(f"{k}={v}" for k, v in detail.items() if not isinstance(v, (list, dict)))
        self._echo(f"✔ {name} ({el}s) {summary}")
        self._write({"stage": name, "status": "ok", "elapsed_s": el, **inputs, **detail})

    def done(self, **detail):
        total = round(time.time() - self._t0, 1)
        self._write({"stage": "_done", "total_s": total, **detail})
        self._echo(f"■ 완료 ({total}s) — 매니페스트: {self.path}")
        return self.path
