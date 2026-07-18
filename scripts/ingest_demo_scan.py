"""스캔 원문 시연 데이터: 스캔풍 PDF(3쪽) 생성 + OCR 텍스트를 페이지 번호와 함께 적재.

실제 업무 흐름(스캔 PDF -> 외부 OCR -> 텍스트)을 재현한다:
- data/samples/스캔_발병경위서_표본.pdf  : 원본 스캔본 역할 (reportlab 생성, 내용 전면 허구)
- OCR 블록(page 1~3)을 청킹·임베딩해 적재하되 orig_path로 위 PDF를 연결
-> 챗봇 출처 클릭 시 스캔 PDF의 해당 페이지가 열린다.

실행: python3 scripts/ingest_demo_scan.py   (멱등: 같은 내용이면 재적재 스킵)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ingestion.types import Block, BlockType
from ingestion.chunker import chunk_blocks
from ingestion.embedder import get_embedder
from ingestion.indexer import index_document

ROOT = Path(__file__).parent.parent
PDF = ROOT / "data" / "samples" / "스캔_발병경위서_표본.pdf"

# 페이지별 본문 (전면 허구 표본 — 개인정보 미포함, 마스킹 데모용 가짜 패턴만)
PAGES = [
    ("발병경위서 (표본 1/3)",
     "신청인 진술: 2021년 10월 방어훈련 기간 중 전투준비태세 물자 적재 검증 업무를 수행하며 "
     "탄약 등 중량물을 반복 운반하던 중 우측 무릎에서 파열음과 함께 통증이 발생하였음. "
     "당시 소속 부대 지휘관에게 즉시 보고하였고 다음 날 의무대를 경유하여 국군병원 외래로 후송되었음."),
    ("의무기록 발췌 (표본 2/3)",
     "국군수도병원 외래(2021-11-15): 우측 슬관절 통증, 인대 손상 의증. "
     "MRI 판독(2021-11-20): 내측 반월상연골 후각부 파열 소견, 급성 외상성 변화 동반. "
     "수술기록(2021-12-23): 관절경하 내측 반월상연골 봉합술 시행, 수술 중 신선 파열면 확인. "
     "연락처 기재: 담당 010-1234-5678 (표본)."),
    ("사실조회 회신 (표본 3/3)",
     "소속 부대 회신: 2021년 10월 방어훈련 기간 신청인에게 전투준비태세 물자 적재 검증 업무가 "
     "부여되었음을 부대일지 및 훈련계획 문서로 확인함. 해당 기간 휴가 내역 없음. "
     "이상의 사실은 요건 심사 참고자료로 제출됨."),
]


_FONT_CANDIDATES = [   # TTF 임베드 우선 (CID 폰트는 뷰어에 언어팩이 없으면 렌더 실패)
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/Library/Fonts/NanumGothic.ttf",
]


def _register_font(pdfmetrics) -> str:
    from reportlab.pdfbase.ttfonts import TTFont
    for fp in _FONT_CANDIDATES:
        if Path(fp).is_file():
            pdfmetrics.registerFont(TTFont("KScan", fp))
            return "KScan"
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont   # 최후 수단
    pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
    return "HYSMyeongJo-Medium"


def make_pdf():
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfgen import canvas

    PDF.parent.mkdir(parents=True, exist_ok=True)
    font = _register_font(pdfmetrics)
    c = canvas.Canvas(str(PDF), pagesize=A4)
    W, H = A4
    for title, body in PAGES:
        # 스캔풍 연출: 옅은 바탕 + 테두리 + 문서 서식
        c.setFillColorRGB(0.97, 0.96, 0.93)
        c.rect(0, 0, W, H, fill=1, stroke=0)
        c.setFillColorRGB(0.1, 0.1, 0.1)
        c.rect(36, 36, W - 72, H - 72, fill=0, stroke=1)
        c.setFont(font, 16)
        c.drawCentredString(W / 2, H - 80, title)
        c.setFont(font, 11)
        y = H - 130
        line = ""
        for word in body.split(" "):
            if len(line) + len(word) + 1 > 42:
                c.drawString(60, y, line); y -= 22; line = word
            else:
                line = f"{line} {word}".strip()
        if line:
            c.drawString(60, y, line)
        c.setFont(font, 9)
        c.drawCentredString(W / 2, 48, "※ 시연용 표본 스캔 문서 — 내용 전면 허구, 개인정보 미포함")
        c.showPage()
    c.save()
    print(f"스캔풍 PDF 생성: {PDF} ({len(PAGES)}쪽)")


def ingest():
    blocks = [Block(BlockType.PARAGRAPH, f"{t} {b}", i + 1, {"confidence": 0.95})
              for i, (t, b) in enumerate(PAGES)]
    chunks = chunk_blocks(blocks)
    emb = get_embedder()
    vecs = emb.encode([c.content for c in chunks])
    n = index_document(f"{PDF}#스캔_발병경위서_표본(OCR)#demo", "ui_upload", chunks, vecs,
                       "demo", orig_path=f"{PDF}#스캔_발병경위서_표본.pdf")
    print("적재 청크:", n if n else "(이미 적재됨 — 스킵)")


if __name__ == "__main__":
    make_pdf()
    ingest()
