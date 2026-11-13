import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ocr_engine import OcrLine  # noqa: E402
from textops import build_text, number_spans, tidy_spaces  # noqa: E402


def L(text, y, w=500, h=20, x=0):
    return OcrLine(text, x, y, w, h)


def test_join_same_paragraph():
    lines = [L("국토교통부는 규제개선 방안을", 0), L("발표하였다.", 22, w=120)]
    assert build_text(lines) == "국토교통부는 규제개선 방안을 발표하였다."


def test_bullet_starts_new_paragraph():
    lines = [L("첫 문단 내용이 이어지다가 끝난다.", 0), L("□ 두 번째 문단", 22), L("ㅇ 세 번째", 44)]
    assert build_text(lines).split("\n") == ["첫 문단 내용이 이어지다가 끝난다.", "□ 두 번째 문단", "ㅇ 세 번째"]


def test_short_previous_line_ends_paragraph():
    lines = [L("긴 줄입니다 긴 줄입니다 긴 줄입니다", 0), L("짧게 끝", 22, w=100), L("다음 문단 시작", 44)]
    assert build_text(lines).split("\n") == ["긴 줄입니다 긴 줄입니다 긴 줄입니다 짧게 끝", "다음 문단 시작"]


def test_big_gap_starts_paragraph():
    lines = [L("앞 문단 끝까지 가득 찬 줄", 0), L("벌어진 줄", 80)]
    assert build_text(lines).split("\n") == ["앞 문단 끝까지 가득 찬 줄", "벌어진 줄"]


def test_keep_original_lines():
    lines = [L("가  나", 0), L("다 라", 22)]
    assert build_text(lines, join_lines=False, tidy=True) == "가 나\n다 라"
    assert build_text(lines, join_lines=False, tidy=False) == "가  나\n다 라"


def test_tidy():
    assert tidy_spaces("A   B ,  C ( D )") == "A B, C (D)"


def test_empty():
    assert build_text([]) == ""


def test_number_spans():
    t = "영업이익 1조2000억원, 전년比 18% 증가, 361조원"
    found = [t[a:b].strip() for a, b in number_spans(t)]
    assert "1조" in found and "2000억" in found and "18%" in found and "361조" in found


def test_table_rows_are_not_joined():
    lines = [L("수원 2.05 13.01 0.42 0.47 0.40 0.45", 0, w=500), L("수원 장안 1.46 8.62 0.28 0.36 0.34 0.33", 22, w=500)]
    assert build_text(lines).split("\n") == ["수원 2.05 13.01 0.42 0.47 0.40 0.45", "수원 장안 1.46 8.62 0.28 0.36 0.34 0.33"]


def test_tab_between_table_cells_is_kept():
    assert tidy_spaces("부장\t김민섭   ☎(053)663-8501") == "부장\t김민섭 ☎(053)663-8501"


def test_bullet_without_space_after_marker():
    lines = [L("앞 문단이 가득 찬 줄입니다 가득 찬 줄입니다", 0), L("ㅇ시도별로는 경기(0.21%)", 22)]
    assert build_text(lines).split("\n")[1].startswith("ㅇ시도별로는")
