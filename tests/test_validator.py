import json

from src.validator import build_checkitems, extract_json, validate


def test_extract_json():
    assert extract_json('说明 {"results": []} 结尾') == {"results": []}
    assert extract_json("没有 JSON") is None
    assert extract_json('{"a": [1,2}') is None


def test_validate_pass_and_fail():
    class FakeLLM:
        def __init__(self, raw):
            self.raw = raw

        def chat(self, system=None, user=None, **kw):
            return self.raw

    good = json.dumps(
        {"results": [{"item": "A", "pass": True, "reason": "有教材依据"}]},
        ensure_ascii=False,
    )
    assert validate("教案", "教材", ["A"], FakeLLM(good))["passed"] is True
    assert validate("教案", "教材", ["A"], FakeLLM("{}"))["passed"] is False
    assert validate("教案", "教材", ["A"], FakeLLM("完全无法解析"))["passed"] is False


def test_build_checkitems_default_contains_minutes():
    config = {"validation": {"checkitems": []}, "stage2": {"total_minutes": 45}}
    items = build_checkitems(config)
    assert any("45" in item for item in items)
    assert any("简体中文" in item for item in items)


def test_build_checkitems_with_template_headings(template_docx):
    from src.template_parser import parse_template

    config = {"validation": {"checkitems": []}, "stage2": {"total_minutes": 0}}
    blocks = parse_template(template_docx)
    items = build_checkitems(config, blocks)
    assert any("二、教学目标" in item for item in items)
    assert not any("分钟" in item for item in items)


def test_build_checkitems_custom_override():
    config = {
        "validation": {"checkitems": ["自定义检查项1"]},
        "stage2": {"total_minutes": 45},
    }
    assert build_checkitems(config) == ["自定义检查项1"]
