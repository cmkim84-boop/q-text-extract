"""문서 파일(PDF · HWPX · HWP)에서 '원본 텍스트'를 그대로 꺼내는 모듈.

이미지 글자 인식(OCR)은 숫자를 틀리게 읽을 수 있지만, 문서 파일 안에는 글자가 이미 텍스트로 들어 있다.
그 텍스트를 읽으면 틀릴 일이 없다. 그래서 PDF·HWPX·HWP 는 OCR 없이 이 모듈로 읽는다.

결과는 ocr_engine.OcrLine 목록으로 돌려줘서, 화면의 줄바꿈·공백 정리(textops.build_text)를 그대로 쓴다.
화면(PySide6)과 무관해서 따로 시험할 수 있다.
"""
from __future__ import annotations

import html
import os
import re
import struct
import tempfile
import zipfile
import zlib
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from ocr_engine import OcrLine

DOC_EXTS = (".pdf", ".hwpx", ".hwp")

_PAGE_NO = re.compile(r"^\s*[-–—]\s*\d{1,3}\s*[-–—]\s*$")  # "- 1 -" 형태의 쪽번호
_PRIVATE = re.compile("[-]")  # 글머리 기호 글꼴이 만드는 깨진 문자


class DocError(Exception):
    """사용자에게 그대로 보여줄 수 있는 추출 실패 사유."""


@dataclass
class DocResult:
    lines: list[OcrLine] = field(default_factory=list)
    kind: str = ""  # pdf / hwpx / hwp
    pages: int = 0  # PDF 쪽수 (그 밖에는 0)
    notes: list[str] = field(default_factory=list)  # 화면에 알려줄 참고 문구


def is_document(path: str) -> bool:
    return path.lower().endswith(DOC_EXTS)


# ---------------------------------------------------------------- PDF
def _clean_pdf_line(t: str) -> str:
    return _PRIVATE.sub("", t).strip()


def from_pdf(path: str) -> DocResult:
    try:
        import pdfplumber
    except Exception as ex:  # noqa: BLE001
        raise DocError(f"PDF 읽기 구성요소를 불러오지 못했어요. ({ex})") from ex
    res = DocResult(kind="pdf")
    bottom = 0.0  # 앞 쪽 마지막 줄의 아래 끝(이어 붙인 좌표)
    try:
        with pdfplumber.open(path) as pdf:
            res.pages = len(pdf.pages)
            for page in pdf.pages:
                lines = []
                for ln in page.extract_text_lines() or []:
                    t = _clean_pdf_line(ln["text"])
                    if t and not _PAGE_NO.match(t):
                        lines.append((t, ln))
                if not lines:
                    continue
                # 쪽을 위아래로 이어 붙인다. 쪽 사이 간격은 없애서, 문장이 다음 쪽으로 넘어가도 이어지게 한다.
                offset = bottom - lines[0][1]["top"] + 1.0 if res.lines else 0.0
                for t, ln in lines:
                    h = max(1.0, ln["bottom"] - ln["top"])
                    res.lines.append(OcrLine(t, ln["x0"], offset + ln["top"], ln["x1"] - ln["x0"], h))
                bottom = offset + lines[-1][1]["bottom"]
    except DocError:
        raise
    except Exception as ex:  # noqa: BLE001
        msg = str(ex).lower()
        if "password" in msg or "encrypt" in msg:
            raise DocError("암호가 걸린 PDF예요. 암호를 해제한 파일을 열어 주세요.") from ex
        raise DocError(f"PDF를 읽지 못했어요. ({type(ex).__name__})") from ex
    if not res.lines:
        res.notes.append("이 PDF에는 글자 정보가 없어요. 스캔한 문서로 보여요. 화면을 캡처해 이미지로 열면 글자를 읽을 수 있어요.")
    return res


# ---------------------------------------------------------------- HWPX
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _hwpx_paragraph(p: ET.Element, out: list[str]) -> None:
    """문단 하나의 글자를 out 에 더한다. 표 안의 글은 한 줄(칸은 탭으로 구분)로 이어서 더한다."""
    buf: list[str] = []
    tables: list[ET.Element] = []
    for run in p:
        if _local(run.tag) != "run":
            continue
        for c in run:
            name = _local(c.tag)
            if name == "t":
                buf.append("".join(c.itertext()))
            elif name == "tab":
                buf.append("\t")
            elif name == "lineBreak":
                buf.append(" ")
            elif name == "tbl":
                tables.append(c)
    text = "".join(buf).strip()
    if text:
        out.append(text)
    for tbl in tables:
        for tr in tbl:
            if _local(tr.tag) != "tr":
                continue
            cells = []
            for tc in tr:
                if _local(tc.tag) != "tc":
                    continue
                parts: list[str] = []
                for sub in tc:
                    if _local(sub.tag) == "subList":
                        for p2 in sub:
                            if _local(p2.tag) == "p":
                                _hwpx_paragraph(p2, parts)
                cells.append(" ".join(parts).strip())
            row = "\t".join(c for c in cells if c)
            if row:
                out.append(row)


def from_hwpx(path: str) -> DocResult:
    paras: list[str] = []
    try:
        with zipfile.ZipFile(path) as z:
            names = sorted(
                (n for n in z.namelist() if re.fullmatch(r"Contents/section\d+\.xml", n)),
                key=lambda n: int(re.findall(r"\d+", n)[0]),
            )
            if not names:
                raise DocError("HWPX 안에서 본문을 찾지 못했어요.")
            for n in names:
                root = ET.fromstring(z.read(n))
                for p in root:
                    if _local(p.tag) == "p":
                        _hwpx_paragraph(p, paras)
    except DocError:
        raise
    except zipfile.BadZipFile as ex:
        raise DocError("HWPX 파일이 아니거나 손상됐어요. 한글에서 다시 저장해 보세요.") from ex
    except ET.ParseError as ex:
        raise DocError("HWPX 본문을 읽지 못했어요. (암호가 걸렸거나 손상된 파일일 수 있어요)") from ex
    res = DocResult(kind="hwpx")
    # 문단마다 넉넉히 떨어뜨려 놓아, 줄바꿈 정리를 켜도 문단이 서로 붙지 않게 한다
    res.lines = [OcrLine(t, 0.0, i * 60.0, 1000.0, 20.0) for i, t in enumerate(paras)]
    if not res.lines:
        res.notes.append("본문 글자를 찾지 못했어요.")
    return res


# ---------------------------------------------------------------- HWP (구형 바이너리)
_HWP_SKIP7 = set(range(1, 10)) | {11, 12} | set(range(14, 24))  # 뒤에 제어 정보 7글자가 더 붙는 글자 코드


def _hwp_text(raw: bytes) -> str:
    out: list[str] = []
    i = 0
    n = len(raw) // 2
    while i < n:
        c = struct.unpack_from("<H", raw, i * 2)[0]
        if c in _HWP_SKIP7:
            if c == 9:
                out.append("\t")
            i += 8
            continue
        i += 1
        if c == 10 or c == 13:
            out.append("\n")
        elif c >= 32:
            out.append(chr(c))
    return "".join(out)


def from_hwp(path: str) -> DocResult:
    try:
        import olefile
    except Exception as ex:  # noqa: BLE001
        raise DocError(f"HWP 읽기 구성요소를 불러오지 못했어요. ({ex})") from ex
    paras: list[str] = []
    try:
        ole = olefile.OleFileIO(path)
        header = ole.openstream("FileHeader").read()
        flags = struct.unpack_from("<I", header, 36)[0]
        if flags & 2:
            raise DocError("암호가 걸린 HWP예요. 암호를 해제한 파일을 열어 주세요.")
        if flags & 4:
            raise DocError("배포용 HWP는 바로 읽을 수 없어요. 한글에서 HWPX 또는 PDF로 저장한 파일을 열어 주세요.")
        compressed = bool(flags & 1)
        sections = sorted((s for s in ole.listdir() if s[0] == "BodyText"), key=lambda s: int(re.findall(r"\d+", s[-1])[0]))
        for s in sections:
            data = ole.openstream(s).read()
            if compressed:
                data = zlib.decompress(data, -15)
            i = 0
            while i + 4 <= len(data):
                h = struct.unpack_from("<I", data, i)[0]
                tag, size = h & 0x3FF, (h >> 20) & 0xFFF
                i += 4
                if size == 0xFFF:
                    size = struct.unpack_from("<I", data, i)[0]
                    i += 4
                if tag == 67:  # HWPTAG_PARA_TEXT
                    for part in _hwp_text(data[i:i + size]).split("\n"):
                        if part.strip():
                            paras.append(part.strip())
                i += size
    except DocError:
        raise
    except Exception as ex:  # noqa: BLE001
        raise DocError(
            "HWP를 읽지 못했어요. 한글에서 HWPX 또는 PDF로 저장한 파일을 열어 주세요. "
            f"({type(ex).__name__})"
        ) from ex
    res = DocResult(kind="hwp")
    res.lines = [OcrLine(t, 0.0, i * 60.0, 1000.0, 20.0) for i, t in enumerate(paras)]
    res.notes.append("구형 HWP는 표·글상자 안의 글이 빠지거나 순서가 다를 수 있어요. HWPX 또는 PDF가 더 정확해요.")
    if not res.lines:
        res.notes.append("본문 글자를 찾지 못했어요.")
    return res


# ---------------------------------------------------------------- 진입점
def extract_document(path: str) -> DocResult:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return from_pdf(path)
    if ext == ".hwpx":
        return from_hwpx(path)
    if ext == ".hwp":
        return from_hwp(path)
    raise DocError("PDF, HWPX, HWP 파일만 열 수 있어요.")


# ---------------------------------------------------------------- 점검용 (빌드 직후 자동 확인)
def make_minimal_pdf(text: str = "Hello PDF 12345") -> bytes:
    """영문 한 줄짜리 가장 작은 PDF. 묶인 프로그램에서 PDF 읽기 부품이 도는지 확인하는 데만 쓴다."""
    stream = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def make_minimal_hwpx(paras: list[str]) -> bytes:
    body = "".join(
        f'<hp:p id="{i}"><hp:run><hp:t>{html.escape(t)}</hp:t></hp:run></hp:p>' for i, t in enumerate(paras)
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
        'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">' + body + "</hs:sec>"
    )
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Contents/section0.xml", xml)
    return buf.getvalue()


def selftest_docs() -> tuple[bool, list[str]]:
    """PDF·HWPX 읽기가 이 프로그램 안에서 실제로 되는지 확인한다. (ok, 로그 줄들)"""
    log: list[str] = []
    ok = True
    with tempfile.TemporaryDirectory() as d:
        pdf = os.path.join(d, "t.pdf")
        with open(pdf, "wb") as f:
            f.write(make_minimal_pdf())
        hwpx = os.path.join(d, "t.hwpx")
        with open(hwpx, "wb") as f:
            f.write(make_minimal_hwpx(["가나다 12,345호", "둘째 문단"]))
        for name, path, want in (("pdf", pdf, "Hello PDF 12345"), ("hwpx", hwpx, "가나다 12,345호")):
            try:
                r = extract_document(path)
                got = " ".join(ln.text for ln in r.lines)
                log.append(f"doc-{name}: {got!r}")
                if want not in got:
                    ok = False
                    log.append(f"FAIL: {name} 문서에서 '{want}' 를 읽지 못했어요")
            except Exception as ex:  # noqa: BLE001
                ok = False
                log.append(f"FAIL: {name}: {type(ex).__name__}: {ex}")
    return ok, log
