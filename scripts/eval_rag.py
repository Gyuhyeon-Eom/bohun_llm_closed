"""RAG 챗봇 엔드투엔드 평가 - mockgen QA로 검색·답변 품질 실측 (v0.2).

benchmark.py가 검색 단독(hit@k)을 측정한다면, 이 스크립트는 chatbot.answer
전체 경로(하이브리드 검색 -> 그래프 근거 -> LLM 답변 -> 반복 검색)를 평가한다.

지표:
  hit@5        기대 문서가 답변 sources에 포함된 비율
  no_evidence  "확인되지 않습니다" 답변 비율 (QA는 전부 근거가 있으므로 낮을수록 좋음)
  citation     출처 표기([문서명 p.N]) 포함 비율
  retried      반복 검색(재질의)이 발동한 비율
  judge        (--judge) LLM 심판의 근거충실 점수 평균 1~5 (동일 로컬 LLM - 참고용)

실행 (실환경: DB 적재 + Ollama 기동 상태):
  LLM_BACKEND=openai .venv/bin/python scripts/eval_rag.py --limit 20
  LLM_BACKEND=openai .venv/bin/python scripts/eval_rag.py --compare
    -> 반복 검색 off/on을 한 프로세스에서 연속 측정 (임베딩 모델 1회 로드)
산출: 콘솔 표 + scripts/eval_result.json (질문별 상세 포함)
"""
import argparse, json, re, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

QA_PATH = Path(__file__).parent.parent / "mockgen" / "corpus" / "qa_set.json"
OUT_PATH = Path(__file__).parent / "eval_result.json"
_CITE = re.compile(r"\[[^\[\]]+ p\.\d+\]")


def run_eval(qa, llm, emb, doc_type: str, judge: bool, retry_max: int) -> dict:
    from services import chatbot
    chatbot.CHAT_RETRY_MAX = retry_max   # 모듈 상수 오버라이드 - 프로세스 재기동 없이 비교
    rows, agg = [], {"hit5": 0, "no_ev": 0, "cite": 0, "retried": 0, "judge_sum": 0, "judged": 0}
    t_total = time.perf_counter()
    for i, item in enumerate(qa):
        t0 = time.perf_counter()
        r = chatbot.answer(item["q"], llm, emb, doc_type=doc_type)
        dt = time.perf_counter() - t0
        ans = r["answer"] or ""
        hit = any(item["expect_doc"] in str(s["source_path"]) for s in r["sources"])
        no_ev = chatbot._NO_EVIDENCE in ans
        cite = bool(_CITE.search(ans))
        row = {"q": item["q"], "expect": item["expect_doc"], "hit5": hit,
               "no_evidence": no_ev, "citation": cite, "retried": r["retried"],
               "rewritten": r["rewritten_query"], "seconds": round(dt, 1),
               "answer": ans[:300]}
        if judge and not no_ev:
            ctx = "\n---\n".join(s["content"][:800] for s in r["sources"])
            try:
                m = re.search(r"[1-5]", llm.generate(
                    "eval_judge", question=item["q"], context=ctx, answer=ans))
                if m:
                    row["judge"] = int(m.group())
                    agg["judge_sum"] += row["judge"]; agg["judged"] += 1
            except Exception as e:
                row["judge_error"] = str(e)[:100]
        agg["hit5"] += hit; agg["no_ev"] += no_ev; agg["cite"] += cite
        agg["retried"] += bool(r["retried"])
        rows.append(row)
        print(f"[retry={retry_max} {i+1}/{len(qa)}] hit={int(hit)} no_ev={int(no_ev)} "
              f"cite={int(cite)} retry={r['retried']} {dt:.0f}s  {item['q'][:40]}", flush=True)
    n = len(rows) or 1
    summary = {"n": len(rows), "doc_type": doc_type, "chat_retry_max": retry_max,
               "hit@5": round(agg["hit5"] / n, 3), "no_evidence": round(agg["no_ev"] / n, 3),
               "citation": round(agg["cite"] / n, 3), "retried": round(agg["retried"] / n, 3),
               "judge_avg": round(agg["judge_sum"] / agg["judged"], 2) if agg["judged"] else None,
               "total_seconds": round(time.perf_counter() - t_total, 1)}
    return {"summary": summary, "detail": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc-type", default="mock_fixed",
                    help="평가 대상 문서군 (benchmark.py 적재 기준 mock_fixed/mock_raw)")
    ap.add_argument("--limit", type=int, default=0, help="QA 수 제한 (0=전체)")
    ap.add_argument("--judge", action="store_true", help="LLM 심판 채점 추가 (답변당 1회 호출 추가)")
    ap.add_argument("--compare", action="store_true",
                    help="반복 검색 off(0) -> on(CHAT_RETRY_MAX) 연속 측정 후 비교")
    args = ap.parse_args()

    from config.settings import LLM_BACKEND, CHAT_RETRY_MAX
    from core.llm_client import get_llm
    from ingestion.embedder import get_embedder

    if LLM_BACKEND == "mock":
        print("경고: LLM_BACKEND=mock - 답변 품질 지표가 무의미합니다. "
              "LLM_BACKEND=openai로 실측하십시오.", file=sys.stderr)
    qa = json.loads(QA_PATH.read_text(encoding="utf-8"))
    if args.limit:
        qa = qa[:args.limit]
    llm, emb = get_llm(), get_embedder()

    modes = [0, CHAT_RETRY_MAX or 1] if args.compare else [CHAT_RETRY_MAX]
    runs = [run_eval(qa, llm, emb, args.doc_type, args.judge, m) for m in modes]
    OUT_PATH.write_text(json.dumps({"llm_backend": LLM_BACKEND, "runs": runs},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
    keys = ("chat_retry_max", "hit@5", "no_evidence", "citation", "retried",
            "judge_avg", "total_seconds")
    print("\n== 요약 ==")
    for k in keys:
        print("  " + f"{k:>14}: " + " vs ".join(str(r["summary"][k]) for r in runs))
    print(f"상세: {OUT_PATH}")


if __name__ == "__main__":
    main()
