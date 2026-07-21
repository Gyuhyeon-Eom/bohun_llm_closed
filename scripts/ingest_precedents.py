# -*- coding: utf-8 -*-
"""판례 적재 — 0721 회의 ②: 법령·매뉴얼 외에 법원 판례를 판단 근거로 포함.

입력 형식 (data/precedents/*.json — 국가법령정보센터·대법원 종합법률정보 등
공개 판례를 수집해 아래 형식으로 저장):
  {"case_no": "대법원 2020두12345", "court": "대법원", "date": "2021-03-25",
   "title": "국가유공자등록거부처분취소", "gist": "판시사항·판결요지 …",
   "body": "판결 전문 …(선택)"}
또는 여러 건 배열. txt도 지원(파일 전체 = body, 파일명 = case_no).

적재: documents/chunks (doc_type='판례') — 기존 RAG 인프라 그대로 사용.
의결서 3.나 '본 건 판단의 전제'와 챗봇 검색에 자동 포함된다.
실행: python3 scripts/ingest_precedents.py [--dir data/precedents]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_items(path):
    if path.lower().endswith(".json"):
        data = json.load(open(path, encoding="utf-8"))
        return data if isinstance(data, list) else [data]
    body = open(path, encoding="utf-8", errors="replace").read()
    stem = os.path.splitext(os.path.basename(path))[0]
    return [{"case_no": stem, "court": None, "date": None, "title": stem,
             "gist": body[:800], "body": body}]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--dir", default="data/precedents")
    args = ap.parse_args()

    files = list(args.files)
    if not files and os.path.isdir(args.dir):
        files = sorted(os.path.join(args.dir, f) for f in os.listdir(args.dir)
                       if f.lower().endswith((".json", ".txt")))
    if not files:
        print(f"판례 파일 없음 — {args.dir}/ 에 *.json(권장)·*.txt를 넣고 재실행하세요.\n"
              "형식은 이 스크립트 상단 주석 참고 (공개 판례 수집분).")
        return

    from ingestion.types import Block, BlockType
    from ingestion.chunker import chunk_blocks
    from ingestion.indexer import index_document
    from ingestion.embedder import get_embedder
    emb = get_embedder()

    n = 0
    for f in files:
        for it in load_items(f):
            case_no = it.get("case_no") or "판례"
            head = " / ".join(filter(None, [case_no, it.get("court"), it.get("date"),
                                            it.get("title")]))
            blocks = [Block(BlockType.PARAGRAPH, f"[{head}]\n{it.get('gist') or ''}", page_no=1)]
            for i, seg in enumerate([(it.get("body") or "")[i:i + 1200]
                                     for i in range(0, len(it.get("body") or ""), 1200)][:20]):
                if seg.strip():
                    blocks.append(Block(BlockType.PARAGRAPH, seg, page_no=i + 2))
            chunks = chunk_blocks(blocks)
            vecs = emb.encode([c.content for c in chunks])
            index_document(f"{f}#판례:{case_no}", "판례", chunks, vecs, "curated")
            n += 1
            print(f"  적재: {head} ({len(chunks)}청크)")
    print(f"완료: 판례 {n}건 → documents(doc_type='판례')")


if __name__ == "__main__":
    main()
