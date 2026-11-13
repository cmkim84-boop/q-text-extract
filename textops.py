"""인식된 줄(OcrLine)을 기사 작업에 쓰기 좋은 글로 다듬는 함수들. 화면과 무관해서 따로 시험할 수 있다."""
from __future__ import annotations

import re
import statistics
from typing import Sequence

# 새 문단이 시작되는 줄의 머리 기호: □ ○ ㅇ ※ * - • ▶ 와  1. / 1) / 가.
_BULLET = re.compile(r"^\s*(?:[□■▢○●◦◎ㅁ※*•▶▷◆◇\-–—∙·ㆍ]\s|ㅇ(?=[\s가-힣\[(<])|[□■▢○●◦◎ㅁ※▶▷◆◇](?=[가-힣\[(<])|\d{1,2}[.)]\s|[가-하][.)]\s|[(（]\d{1,2}[)）]\s)")
_SPACE_RUN = re.compile(r"[ \u00a0\u3000]+")  # 탭(표 칸 구분)은 그대로 둔다

# 숫자 점검용: 숫자 + 단위 (콤마·소수점 포함). 기사에서 틀리면 곤란한 값들을 눈에 띄게 한다.
NUMBER_PATTERN = re.compile(
    r"\d[\d,]*(?:\.\d+)?\s?(?:%p|%포인트|%|퍼센트포인트|퍼센트|조|억|만|천|백|원|달러|위안|엔|명|개|건|배|곳|년|월|일|분기|세|kg|km|m|cm|mm|㎡|평)?"
)


_NUM_TOKEN = re.compile(r"[‘'’]?-?[\d.,]+[%년주]?")


def is_table_like(text: str) -> bool:
    """숫자 칸이 많은 줄(표·차트 눈금)이거나 탭으로 칸이 나뉜 줄. 문장이 아니므로 앞뒤 줄과 잇지 않는다."""
    if "\t" in text:
        return True
    toks = text.split()
    return len(toks) >= 4 and sum(bool(_NUM_TOKEN.fullmatch(t)) for t in toks) / len(toks) >= 0.4


def _is_new_paragraph(prev, cur, med_h: float, max_w: float) -> bool:
    if _BULLET.match(cur.text):
        return True
    if is_table_like(prev.text) or is_table_like(cur.text):
        return True
    gap = cur.y - (prev.y + prev.h)
    if med_h > 0 and gap > 0.9 * med_h:  # 줄 사이가 유난히 벌어짐
        return True
    if max_w > 0 and prev.w < 0.62 * max_w:  # 앞 줄이 짧게 끝남 = 문단 끝
        return True
    return False


def tidy_spaces(text: str) -> str:
    """연속 공백을 하나로, 줄 앞뒤 공백 제거, 빈 줄은 최대 한 줄만."""
    out = []
    for ln in text.split("\n"):
        ln = _SPACE_RUN.sub(" ", ln).strip()
        ln = re.sub(r"\s+([,.;:!?)\]」』])", r"\1", ln)
        ln = re.sub(r"([(\[「『])\s+", r"\1", ln)
        out.append(ln)
    res = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", res).strip("\n")


def build_text(lines: Sequence, join_lines: bool = True, tidy: bool = True) -> str:
    """인식된 줄들을 하나의 글로.

    join_lines=True : 같은 문단 안의 줄바꿈을 없애고 한 문단으로 잇는다 (문단은 그대로 줄바꿈).
    join_lines=False: 이미지에 보이던 줄 그대로.
    tidy=True       : 공백 정리.
    """
    if not lines:
        return ""
    if not join_lines:
        text = "\n".join(ln.text for ln in lines)
        return tidy_spaces(text) if tidy else text

    med_h = statistics.median([ln.h for ln in lines if ln.h > 0] or [0])
    max_w = max((ln.w for ln in lines), default=0)
    paras: list[list[str]] = [[lines[0].text.strip()]]
    for prev, cur in zip(lines, lines[1:]):
        if _is_new_paragraph(prev, cur, med_h, max_w):
            paras.append([cur.text.strip()])
        else:
            paras[-1].append(cur.text.strip())
    text = "\n".join(" ".join(p) for p in paras)
    return tidy_spaces(text) if tidy else text


def number_spans(text: str) -> list[tuple[int, int]]:
    """숫자(+단위) 위치 [(시작, 끝)]. 결과 칸에서 색으로 강조해 눈으로 대조하기 쉽게 한다."""
    return [(m.start(), m.end()) for m in NUMBER_PATTERN.finditer(text)]
