import json

import pytest

import aidraft

FAKE_TEXT = """===국문 초안===
유형 판정: 공공 자료, 표준 기사화

정부는 13일 브리핑을 열고 밝혔다.
===영문 초안===
The government said on Friday.
===분류·검증===
원문이 제시한 사실
브리핑 개최
발표 주체의 주장
해당 없음
검증이 필요한 대목
해당 없음
추가 취재가 필요한 대목
해당 없음
===제목===
제목 하나
제목 둘
제목 셋"""


def fake_post(usage=None, text=FAKE_TEXT, stop="end_turn"):
    seen = {}

    def _post(key, payload, timeout=0):
        seen["key"], seen["payload"] = key, payload
        return {"content": [{"type": "text", "text": text}], "stop_reason": stop,
                "usage": usage or {"input_tokens": 20000, "output_tokens": 3000}}

    return _post, seen


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setattr(aidraft, "data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(aidraft, "user_prompts_dir", lambda: str(tmp_path / "prompts"))
    monkeypatch.setattr(aidraft, "usage_log_path", lambda: str(tmp_path / "ai_usage.csv"))


def test_guidelines_load():
    full, n_full, c_full = aidraft.load_guidelines(True)
    core, n_core, c_core = aidraft.load_guidelines(False)
    assert n_full == 7 and n_core == 4 and c_full > c_core > 8000
    assert "하네스 규약" not in full


def test_parse_sections_and_english_excluded():
    s = aidraft.parse_sections(FAKE_TEXT)
    assert set(s) == {"ko", "en", "chk", "title"}
    assert s["title"].splitlines()[0] == "제목 하나"
    s2 = aidraft.parse_sections("===국문 초안===\n본문\n===영문 초안===\n영문 제외\n===제목===\n가")
    assert "en" not in s2 and s2["ko"] == "본문"
    assert aidraft.parse_sections("표지 없는 글") == {"ko": "표지 없는 글"}


def test_cost_haiku45():
    u = aidraft.Usage(input_tokens=20000, output_tokens=3000)
    usd, krw = aidraft.calc_cost("claude-haiku-4-5", u, 1400)
    assert usd == pytest.approx(0.035)  # 0.02 + 0.015
    assert krw == pytest.approx(49)
    assert aidraft.calc_cost("claude-haiku-4-5-20251001", u, 1400)[0] == pytest.approx(0.035)  # 날짜 붙은 ID


def test_cost_with_cache_and_unknown_model():
    u = aidraft.Usage(input_tokens=1000, cache_write=10000, cache_read=10000, output_tokens=0)
    usd, _ = aidraft.calc_cost("claude-sonnet-5-5", u, 1400)
    assert usd == pytest.approx((1000 + 12500 + 1000) * 2 / 1e6)
    assert aidraft.calc_cost("some-new-model", u, 1400) == (None, None)


def test_generate_builds_cached_request_and_logs(monkeypatch, tmp_path):
    post, seen = fake_post({"input_tokens": 500, "cache_creation_input_tokens": 15000, "cache_read_input_tokens": 0, "output_tokens": 2500})
    monkeypatch.setattr(aidraft, "_post", post)
    res = aidraft.generate("보도자료 원문 11월 13일 65건", "standard", True, "sk-test", "claude-haiku-4-5", True, 1400, "a.pdf")
    p = seen["payload"]
    assert p["system"][-1]["cache_control"] == {"type": "ephemeral"}  # 지침은 캐시
    assert "보도자료 원문 11월 13일 65건" in p["messages"][0]["content"] and "영문 초안: 포함" in p["messages"][0]["content"]
    assert seen["key"] == "sk-test" and res.sections["ko"].startswith("유형 판정")
    assert res.usage.total_input == 15500 and res.cost_krw is not None
    rows = (tmp_path / "ai_usage.csv").read_text(encoding="utf-8-sig").strip().splitlines()
    assert len(rows) == 2 and "a.pdf" in rows[1] and "claude-haiku-4-5" in rows[1]
    assert "입력 15,500" in aidraft.format_cost(res) and "원" in aidraft.format_cost(res)


def test_english_off_and_max_tokens(monkeypatch):
    post, seen = fake_post(stop="max_tokens")
    monkeypatch.setattr(aidraft, "_post", post)
    res = aidraft.generate("글", "brief", False, "k", "claude-haiku-4-5", False)
    assert "영문 초안: 제외" in seen["payload"]["messages"][0]["content"]
    assert "끊겼어요" in " ".join(res.notes)


def test_errors(monkeypatch):
    with pytest.raises(aidraft.AiError):
        aidraft.generate("글", "standard", True, "  ")
    with pytest.raises(aidraft.AiError):
        aidraft.generate("   ", "standard", True, "k")
    assert "API 키" in aidraft._friendly_error(401, "{}")
    assert "크레딧" in aidraft._friendly_error(400, json.dumps({"error": {"message": "Your credit balance is too low"}}))
    assert "모델" in aidraft._friendly_error(404, "{}")
    monkeypatch.setattr(aidraft, "_post", lambda *a, **k: {"content": [], "usage": {}})
    with pytest.raises(aidraft.AiError):
        aidraft.generate("글", "standard", True, "k")


def test_user_prompts_override(tmp_path):
    d = aidraft.ensure_user_prompts()
    assert (tmp_path / "prompts" / "core").is_dir()
    (tmp_path / "prompts" / "core" / "99-내지침.md").write_text("내 지침", encoding="utf-8")
    text, n, _ = aidraft.load_guidelines(False)
    assert "내 지침" in text and n == 5 and d.endswith("prompts")
