# -*- coding: utf-8 -*-
"""심의검토서 hwpx(한글) 산출 — 표준 라이브러리만 사용, 외부 패키지·한컴오피스 불필요.

HWPX는 국가표준 KS X 6101(OWPML)의 zip+XML 포맷이라 라이브러리 없이 생성 가능
(폐쇄망 반입물 0). data/templates/hwpx_blank/ 는 실물 한글 문서에서 본문만 걷어낸
구조 골격(스타일·페이지 설정 포함)이며, 여기에 문단을 채워 zip으로 조립한다.

검토의견 25·26번(1·4분과) 대응: 심의검토서 초안 다운로드에 한글(hwpx) 포맷 추가.
"""
import json
import re
import zipfile
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

TPL = Path(__file__).resolve().parent.parent / "data" / "templates" / "hwpx_blank"


def _para(text: str, attrs: str, char_ref: str) -> str:
    """본문 한 줄 → OWPML 문단. 들여쓰기 공백은 그대로 보존(정형화틀 계층 표현)."""
    return (f"<hp:p {attrs}><hp:run charPrIDRef=\"{char_ref}\">"
            f"<hp:t>{escape(text)}</hp:t></hp:run></hp:p>")


def build_hwpx(title: str, body: str) -> bytes:
    """제목·본문 텍스트 → hwpx 바이트. 본문은 줄 단위로 문단화(빈 줄 = 빈 문단)."""
    meta = json.loads((TPL / "meta.json").read_text(encoding="utf-8"))
    attrs, char_ref = meta["para_attrs"], meta["char_ref"]

    head = (TPL / "section_head.xml").read_text(encoding="utf-8")
    # 골격 첫 문단(페이지 설정 secPr 보유)의 빈 hp:t에 제목 주입
    head = head.replace("<hp:t></hp:t>", f"<hp:t>{escape(title)}</hp:t>", 1)
    paras = [_para(ln, attrs, char_ref) for ln in body.splitlines()]
    section = head + "".join(paras) + (TPL / "section_tail.xml").read_text(encoding="utf-8")

    hpf = (TPL / "Contents" / "content.hpf").read_text(encoding="utf-8")
    hpf = hpf.replace("<opf:title/>", f"<opf:title>{escape(title)}</opf:title>", 1)

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # mimetype은 관례상 첫 엔트리·무압축 (EPUB/OWPML 컨테이너 규약)
        z.writestr(zipfile.ZipInfo("mimetype"), (TPL / "mimetype").read_bytes(),
                   compress_type=zipfile.ZIP_STORED)
        z.writestr("version.xml", (TPL / "version.xml").read_bytes())
        z.writestr("settings.xml", (TPL / "settings.xml").read_bytes())
        for f in ("container.xml", "manifest.xml", "container.rdf"):
            z.writestr(f"META-INF/{f}", (TPL / "META-INF" / f).read_bytes())
        z.writestr("Contents/content.hpf", hpf)
        z.writestr("Contents/header.xml", (TPL / "Contents" / "header.xml").read_bytes())
        z.writestr("Contents/section0.xml", section)
    return buf.getvalue()


def extract_text(hwpx_bytes: bytes) -> str:
    """검증용 역추출 — 생성물이 스스로 읽히는지 라운드트립 확인(테스트에서 사용)."""
    with zipfile.ZipFile(BytesIO(hwpx_bytes)) as z:
        xml = z.read("Contents/section0.xml").decode("utf-8")
    out, buf = [], []
    for m in re.finditer(r"<hp:t(?:\s[^>]*)?>(.*?)</hp:t>|</hp:p>", xml, re.S):
        if m.group(0) == "</hp:p>":
            out.append("".join(buf)); buf = []
        elif m.group(1):
            buf.append(m.group(1))
    text = "\n".join(out)
    return (text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&"))
