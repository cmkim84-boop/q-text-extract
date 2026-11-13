import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import docextract  # noqa: E402
from textops import build_text  # noqa: E402


def _write(d, name, data):
    p = os.path.join(d, name)
    with open(p, "wb") as f:
        f.write(data)
    return p


def test_pdf_text_is_read_as_is():
    with tempfile.TemporaryDirectory() as d:
        r = docextract.extract_document(_write(d, "a.pdf", docextract.make_minimal_pdf("Total 14,152 up 64.0%")))
    assert r.kind == "pdf" and r.pages == 1
    assert build_text(r.lines) == "Total 14,152 up 64.0%"


def test_hwpx_paragraphs_stay_separate():
    with tempfile.TemporaryDirectory() as d:
        r = docextract.extract_document(_write(d, "a.hwpx", docextract.make_minimal_hwpx(["첫 문단 14,152호", "둘째 문단 64.0%"])))
    assert build_text(r.lines).split("\n") == ["첫 문단 14,152호", "둘째 문단 64.0%"]


def test_page_number_lines_are_dropped():
    assert docextract._PAGE_NO.match("- 3 -") and docextract._PAGE_NO.match("– 12 –")
    assert not docextract._PAGE_NO.match("- 3개 -")


def test_hwp_text_skips_control_payload():
    import struct

    # 글자 '가', 확장 제어(코드 11 + 7글자 정보), 글자 '나', 문단 끝(13)
    raw = struct.pack("<H", 0xAC00) + struct.pack("<H", 11) + b"\x01\x00" * 7 + struct.pack("<H", 0xB098) + struct.pack("<H", 13)
    assert docextract._hwp_text(raw).replace("\n", "") == "가나"


def test_wrong_type_and_broken_files_give_readable_errors():
    with tempfile.TemporaryDirectory() as d:
        with pytest.raises(docextract.DocError):
            docextract.extract_document(_write(d, "a.hwpx", b"not a zip"))
        with pytest.raises(docextract.DocError):
            docextract.extract_document(_write(d, "a.txt", b"x"))


def test_selftest_docs_passes():
    ok, log = docextract.selftest_docs()
    assert ok, log
