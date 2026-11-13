"""AI 기사 초안 모듈 - 추출한 보도자료 글을 Claude API 로 보내 아주뉴스 지침에 맞는 기사 초안을 받는다.

- 서버 없이 내 API 키 하나로 직접 호출한다. (개인용)
- 화면 코드는 generate() 와 test_connection() 만 알면 된다.
- 지침 파일은 prompts 폴더의 .md 파일이다. 스킬이 바뀌면 파일만 바꿔 넣으면 된다.
- 호출마다 토큰 수와 예상 비용(원)을 계산해 돌려주고, 사용 기록(CSV)에 한 줄씩 남긴다.
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-haiku-5-5"
DEFAULT_KRW_RATE = 1400.0  # 1달러 = 1,400원 (설정에서 바꿀 수 있다)
MAX_TOKENS = 8000
TIMEOUT_SEC = 240

# (표시 이름, 모델 ID) — 설정 창 목록. 목록에 없는 ID 는 직접 입력해도 된다.
MODELS = [
    ("Claude Haiku 4.5 (비교용)", "claude-haiku-4-5"),
    ("Claude Haiku 5.5 (기본 · 가장 저렴)", "claude-haiku-5-5"),
    ("Claude Sonnet 5.5 (균형)", "claude-sonnet-5-5"),
    ("Claude Opus 5.5 (최고 품질)", "claude-opus-5-5"),
]

# 100만 토큰당 달러 (입력, 출력). 공식 가격표를 직접 확인해서 바꿀 수 있게 이 표만 고치면 된다.
# Haiku 5.5 는 프롬프트 10만 토큰 이하 기준이다. (넘으면 단가가 5배라 이 프로그램에서는 거의 해당 없음)
PRICES = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
CACHE_WRITE_MULT = 1.25  # 캐시 쓰기(5분)는 입력 단가의 1.25배
CACHE_READ_MULT = 0.10  # 캐시 읽기는 입력 단가의 0.1배 (모델에 따라 더 싸다. 여기서는 보수적으로 계산)

# 처리 방식: 키 -> (표시 이름, 설명, 모델에게 줄 지시)
MODES = {
    "standard": ("표준 기사화", "발표와 사실을 나눠서, 일반적인 길이로",
                 "표준 기사화. 스트레이트 기사 800~1200자 분량으로 쓴다. 원문이 짧으면 기사도 짧게 쓴다."),
    "brief": ("짧은 단신", "원문이 짧거나 간단히 쓸 때",
              "짧은 단신. 400자 안팎으로 쓴다."),
    "explain": ("배경을 더한 해설", "배경 설명을 덧붙여 길게",
                "배경을 더한 해설. 1500~3000자 분량으로 쓴다. 배경은 원문 안의 내용으로만 채우고, 원문 밖 사실이 꼭 필요하면 대괄호로 표시해 기자가 확인하게 한다."),
}

SECTION_ORDER = ["ko", "en", "chk", "title"]
SECTION_LABELS = {"ko": "국문 초안", "en": "영문 초안", "chk": "분류·검증", "title": "제목 3안"}

PREAMBLE = """너는 아주경제 뉴스룸의 기사 작성 보조다. 지금부터 보도자료 원문을 받아 아래 지침 문서들에 따라 기사 초안을 쓴다.

[이 프로그램에서의 약속]
- 아래 지침 문서는 원래 대화형 도구에서 쓰던 것이다. 파일 읽기, 선택 박스, 작업 카드, 다음 단계 질문, 후속 명령 안내는 모두 하지 않는다. 지침 중 기사 작성 규칙, 표기 규칙, 금지 사항만 따른다.
- 처리 방식과 영문 초안 포함 여부는 사용자가 이미 골랐다. 사용자 메시지에 적힌 대로 쓰고 되묻지 않는다.
- 원문 밖 사실은 쓰지 않는다. 확인되지 않은 값은 지어 채우지 않고 대괄호로 표시한다.
- 별표, 굵게 표시용 마크다운 기호, 글머리 기호, 표를 쓰지 않는다. 깨끗한 줄글로만 쓴다.

[출력 형식] 아래 표지 줄을 그대로, 이 순서로 쓴다. 표지 줄 앞뒤에 다른 설명을 붙이지 않는다.
===국문 초안===
첫 줄에 유형 판정을 한 줄로 쓴다(예: 유형 판정: 공공 자료, 표준 기사화). 한 줄을 비우고 국문 기사 본문을 쓴다. 제목은 여기에 쓰지 않는다.
===영문 초안===
영문 초안이 필요하면 영문 기사 본문을 쓴다. 필요 없으면 영문 제외 한 줄만 쓴다.
===분류·검증===
아래 네 갈래를 이 순서로 쓴다. 각 갈래는 갈래 이름 한 줄 뒤에 항목을 한 줄씩 쓴다. 항목이 없으면 해당 없음이라고 쓴다.
원문이 제시한 사실
발표 주체의 주장
검증이 필요한 대목
추가 취재가 필요한 대목
===제목===
서로 다른 방향의 제목 세 안을 한 줄씩 쓴다.
"""


# ---------------------------------------------------------------- 폴더 · 설정 위치
def bundled_prompts_dir() -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "prompts")


def data_dir() -> str:
    """사용 기록과 내 지침 파일을 두는 폴더 (내 계정에만 있다)."""
    if sys.platform == "win32":
        root = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        root = os.path.join(os.path.expanduser("~"), ".config")
    d = os.path.join(root, "QTextExtract")
    os.makedirs(d, exist_ok=True)
    return d


def user_prompts_dir() -> str:
    return os.path.join(data_dir(), "prompts")


def ensure_user_prompts() -> str:
    """내 지침 폴더가 없으면 기본 지침을 복사해서 만든다. 이후에는 이 폴더의 파일을 쓴다."""
    dst = user_prompts_dir()
    if not os.path.isdir(dst):
        shutil.copytree(bundled_prompts_dir(), dst)
    return dst


def usage_log_path() -> str:
    return os.path.join(data_dir(), "ai_usage.csv")


# ---------------------------------------------------------------- 지침 읽기
def load_guidelines(full: bool = True) -> tuple[str, int, int]:
    """지침 문서를 하나의 글로 묶어 (글, 파일 수, 글자 수)를 돌려준다. full=False 면 core 폴더만 쓴다."""
    root = user_prompts_dir() if os.path.isdir(user_prompts_dir()) else bundled_prompts_dir()
    folders = ["core", "extra"] if full else ["core"]
    parts: list[str] = []
    for fo in folders:
        d = os.path.join(root, fo)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if name.lower().endswith(".md"):
                with open(os.path.join(d, name), encoding="utf-8") as f:
                    parts.append(f"<지침 문서: {name}>\n{f.read().strip()}\n</지침 문서>")
    text = "\n\n".join(parts)
    return text, len(parts), len(text)


# ---------------------------------------------------------------- 요청 만들기
def build_request(text: str, mode: str, english: bool, model: str, full: bool = True) -> dict:
    guide, _n, _c = load_guidelines(full)
    if not guide:
        raise AiError("지침 파일(prompts 폴더)을 찾지 못했어요. 설정에서 지침 폴더를 확인해 주세요.")
    _label, _desc, how = MODES[mode]
    user = (
        f"처리 방식: {how}\n"
        f"영문 초안: {'포함' if english else '제외(영문 초안 자리에는 영문 제외 한 줄만 쓴다)'}\n\n"
        f"[보도자료 원문]\n{text.strip()}"
    )
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": [
            {"type": "text", "text": PREAMBLE},
            # 지침은 매번 같으므로 캐시해 두면 연달아 쓸 때 입력 비용이 크게 줄어든다.
            {"type": "text", "text": guide, "cache_control": {"type": "ephemeral"}},
        ],
        "messages": [{"role": "user", "content": user}],
    }


# ---------------------------------------------------------------- 호출
class AiError(Exception):
    """사용자에게 그대로 보여줄 수 있는 실패 사유."""


def _friendly_error(code: int, body: str) -> str:
    msg = ""
    try:
        msg = json.loads(body).get("error", {}).get("message", "")
    except Exception:  # noqa: BLE001
        msg = body[:200]
    low = msg.lower()
    if code == 401:
        return "API 키가 올바르지 않아요. 설정에서 키를 다시 확인해 주세요."
    if code == 403:
        return "이 키로는 쓸 수 없어요. 키 권한을 확인해 주세요."
    if code == 404:
        return f"모델 이름을 찾지 못했어요. 설정의 모델을 확인해 주세요. ({msg})"
    if code == 429:
        return "요청이 몰렸거나 사용 한도에 닿았어요. 잠시 뒤에 다시 해 주세요."
    if code == 400 and "credit" in low:
        return "API 크레딧이 부족해요. 콘솔에서 크레딧을 충전해 주세요."
    if code in (500, 502, 503, 529):
        return "Claude 서버가 바빠요. 잠시 뒤에 다시 해 주세요."
    return f"요청이 실패했어요. ({code}) {msg}"


def _post(key: str, payload: dict, timeout: int = TIMEOUT_SEC) -> dict:
    """API 호출 한 번. 시험에서는 이 함수를 바꿔 끼운다."""
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"x-api-key": key, "anthropic-version": API_VERSION, "content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise AiError(_friendly_error(e.code, e.read().decode("utf-8", "replace"))) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise AiError(f"인터넷에 연결하지 못했어요. 회사 방화벽이나 프록시를 확인해 주세요. ({e})") from e


# ---------------------------------------------------------------- 결과 · 비용
@dataclass
class Usage:
    input_tokens: int = 0
    cache_write: int = 0
    cache_read: int = 0
    output_tokens: int = 0

    @property
    def total_input(self) -> int:
        return self.input_tokens + self.cache_write + self.cache_read


@dataclass
class AiResult:
    sections: dict[str, str] = field(default_factory=dict)
    raw: str = ""
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    seconds: float = 0.0
    cost_usd: float | None = None
    cost_krw: float | None = None
    stop_reason: str = ""
    notes: list[str] = field(default_factory=list)


def _base_model(model: str) -> str:
    return re.sub(r"-\d{8}$", "", model.strip())  # claude-haiku-4-5-20251001 -> claude-haiku-4-5


def calc_cost(model: str, u: Usage, krw_rate: float) -> tuple[float | None, float | None]:
    p = PRICES.get(_base_model(model))
    if not p:
        return None, None
    pin, pout = p
    usd = ((u.input_tokens + u.cache_write * CACHE_WRITE_MULT + u.cache_read * CACHE_READ_MULT) * pin + u.output_tokens * pout) / 1e6
    return usd, usd * krw_rate


_NORM_KEYS = {"국문초안": "ko", "영문초안": "en", "분류검증": "chk", "제목": "title"}
_MARK = re.compile(r"^={3}\s*(.+?)\s*={3}\s*$", re.M)


def parse_sections(raw: str) -> dict[str, str]:
    """===국문 초안=== 같은 표지 줄로 글을 나눈다. 표지가 없으면 통째로 국문 초안에 넣는다."""
    marks = list(_MARK.finditer(raw))
    out: dict[str, str] = {}
    for i, m in enumerate(marks):
        norm = re.sub(r"[\s·ㆍ]", "", m.group(1))
        key = next((v for k, v in _NORM_KEYS.items() if norm.startswith(k)), None)
        if key is None:
            continue
        end = marks[i + 1].start() if i + 1 < len(marks) else len(raw)
        out[key] = raw[m.end():end].strip()
    if not out and raw.strip():
        out["ko"] = raw.strip()
    en = out.get("en", "")
    if en and re.fullmatch(r"\(?영문 제외\)?\.?", en.strip()):
        out.pop("en")
    return out


def _log_usage(res: AiResult, meta: dict) -> None:
    try:
        path = usage_log_path()
        new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["시각", "파일", "모델", "지침범위", "처리방식", "영문", "입력토큰", "캐시쓰기", "캐시읽기", "출력토큰", "비용(USD)", "비용(원)", "초"])
            u = res.usage
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), meta.get("file", ""), res.model, meta.get("scope", ""), meta.get("mode", ""),
                        "O" if meta.get("english") else "X", u.input_tokens, u.cache_write, u.cache_read, u.output_tokens,
                        "" if res.cost_usd is None else f"{res.cost_usd:.5f}", "" if res.cost_krw is None else f"{res.cost_krw:.1f}", f"{res.seconds:.1f}"])
    except OSError:
        pass  # 기록을 못 남겨도 초안은 보여준다


def generate(text: str, mode: str, english: bool, key: str, model: str = DEFAULT_MODEL, full: bool = True,
             krw_rate: float = DEFAULT_KRW_RATE, file_name: str = "") -> AiResult:
    if not key.strip():
        raise AiError("API 키가 없어요. 설정에서 키를 넣어 주세요.")
    if not text.strip():
        raise AiError("보낼 글이 없어요.")
    payload = build_request(text, mode, english, model, full)
    t0 = time.perf_counter()
    data = _post(key.strip(), payload)
    sec = time.perf_counter() - t0
    raw = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    us = data.get("usage", {}) or {}
    u = Usage(int(us.get("input_tokens", 0) or 0), int(us.get("cache_creation_input_tokens", 0) or 0),
              int(us.get("cache_read_input_tokens", 0) or 0), int(us.get("output_tokens", 0) or 0))
    res = AiResult(parse_sections(raw), raw, u, model, sec, stop_reason=str(data.get("stop_reason", "")))
    res.cost_usd, res.cost_krw = calc_cost(model, u, krw_rate)
    if res.stop_reason == "max_tokens":
        res.notes.append("글이 길어서 중간에 끊겼어요. 처리 방식을 짧게 하거나 영문을 빼 보세요.")
    if not raw.strip():
        raise AiError("AI가 빈 답을 보냈어요. 다시 시도해 주세요.")
    _log_usage(res, {"file": file_name, "scope": "전체" if full else "핵심", "mode": MODES[mode][0], "english": english})
    return res


def test_connection(key: str, model: str) -> str:
    """키와 모델이 되는지 아주 짧게 한 번 불러 본다. 성공하면 빈 글, 실패하면 AiError."""
    payload = {"model": model, "max_tokens": 8, "messages": [{"role": "user", "content": "안녕"}]}
    _post(key.strip(), payload, timeout=30)
    return ""


def format_cost(res: AiResult) -> str:
    u = res.usage
    cache = f" (캐시 읽기 {u.cache_read:,})" if u.cache_read else ""
    cost = "단가 미등록 모델이라 비용은 계산하지 못했어요" if res.cost_krw is None else f"약 {res.cost_krw:,.0f}원 (${res.cost_usd:.4f})"
    return f"입력 {u.total_input:,}{cache} · 출력 {u.output_tokens:,} · {cost}"


def selftest_prompts() -> tuple[bool, list[str]]:
    """묶인 프로그램 안에 지침 파일이 제대로 들어 있는지 확인한다."""
    text, n, c = load_guidelines(True)
    ok = n >= 7 and c > 15000
    return ok, [f"prompts: {n}개 파일 · {c:,}자" if ok else f"FAIL: 지침 파일이 부족해요 ({n}개 · {c}자)"]
