"""抽取技能的字段形状兼容。

背景：模型有时把 `requirements` 输出成**对象数组**（实测形如
`{"id": "REQ-001", "module": "...", ...}`），而字段声明是 `list[str]`。
直接 model_validate 会整体校验失败，导致**抽取的全部字段**一起降级到启发式——
一个格式偏差把整条链路的质量都拖下去了。
"""

import json

from requirement_agent.skills.extract_skill import ExtractSkill, _coerce_str_list

RAW = "日常小习惯打卡小程序：支持快速打卡、统计图表、个人中心数据导出图片。"


class FakeProvider:
    configured = True

    def __init__(self, text: str) -> None:
        self.text = text

    def is_configured(self) -> bool:
        return True

    def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        return self.text


def _payload(**overrides) -> str:
    data = {
        "requirement_title": "小习惯打卡小程序",
        "summary": "支持快速打卡与统计导出",
        "business_domain": "general",
        "priority": "medium",
        "tags": ["打卡", "统计"],
        "requirements": ["支持快速打卡", "生成统计图表"],
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


# ── _coerce_str_list 的形状兼容 ─────────────────────────────────────────


def test_coerce_keeps_plain_strings() -> None:
    assert _coerce_str_list(["甲", " 乙 ", ""]) == ["甲", "乙"]


def test_coerce_takes_text_key_from_objects() -> None:
    items = [{"id": "REQ-001", "module": "打卡", "text": "点击即可完成今日打卡"}]

    assert _coerce_str_list(items) == ["点击即可完成今日打卡"]


def test_coerce_joins_values_when_no_known_key() -> None:
    """认不出常用键时也要留住内容，不能整条丢掉。"""
    items = [{"module": "统计", "rule": "按周聚合"}]

    joined = _coerce_str_list(items)
    assert len(joined) == 1
    assert "统计" in joined[0] and "按周聚合" in joined[0]


def test_coerce_handles_nested_and_non_list_values() -> None:
    assert _coerce_str_list(None) == []
    assert _coerce_str_list("不是列表") == []
    assert _coerce_str_list([{"text": "甲"}, "乙", 3]) == ["甲", "乙", "3"]


# ── 端到端：对象数组不再拖垮整个抽取 ────────────────────────────────────


def test_extract_survives_object_shaped_requirements() -> None:
    """核心回归：这条以前会让整个抽取降级到启发式。"""
    provider = FakeProvider(
        _payload(
            requirements=[
                {"id": "REQ-001", "module": "打卡", "text": "点击即可完成今日打卡"},
                {"id": "REQ-002", "module": "统计", "text": "生成周/月统计图表"},
            ]
        )
    )

    result = ExtractSkill(provider=provider).extract(RAW, source_type="web")

    # 模型给的标题保住了 —— 若降级到启发式，标题会是原文首行
    assert result.requirement_title == "小习惯打卡小程序"
    assert result.requirements == ["点击即可完成今日打卡", "生成周/月统计图表"]
    assert all(isinstance(item, str) for item in result.requirements)
    assert result.tags == ["打卡", "统计"]


def test_extract_survives_object_shaped_tags() -> None:
    provider = FakeProvider(_payload(tags=[{"name": "打卡"}, {"name": "统计"}]))

    result = ExtractSkill(provider=provider).extract(RAW, source_type="web")

    assert result.tags == ["打卡", "统计"]
    assert result.requirement_title == "小习惯打卡小程序"


def test_extract_falls_back_only_when_shape_is_unusable() -> None:
    """字段缺失仍按原逻辑兜底，但不再是「因一个字段格式不对而全盘降级」。"""
    provider = FakeProvider(_payload(requirements=[], tags=[]))

    result = ExtractSkill(provider=provider).extract(RAW, source_type="web")

    assert result.requirement_title == "小习惯打卡小程序"  # 模型字段仍在
    assert result.requirements  # 空列表 → 回退到启发式的条目


def test_extract_still_falls_back_on_broken_json() -> None:
    provider = FakeProvider("这不是 JSON")

    result = ExtractSkill(provider=provider).extract(RAW, source_type="web")

    assert result.requirement_title  # 启发式兜底仍然工作
    assert result.requirements


def test_extract_parses_modules_structure() -> None:
    """模型给出 [{module, items}] 时，modules 要被结构化解析（模块化，D 批）。"""
    provider = FakeProvider(
        _payload(
            modules=[
                {"module": "登录", "items": ["短信验证码登录", "账号锁定策略"]},
                {"module": "报表", "items": ["按部门导出"]},
            ]
        )
    )

    result = ExtractSkill(provider=provider).extract(RAW, source_type="web")

    assert result.requirement_title == "小习惯打卡小程序"  # 模型字段仍在
    assert len(result.modules) == 2
    assert result.modules[0].module == "登录"
    assert result.modules[0].items == ["短信验证码登录", "账号锁定策略"]
    assert result.modules[1].module == "报表"


def test_extract_modules_empty_when_model_gives_none() -> None:
    """没给 modules 时为空，退回扁平 requirements——不强制所有模型都分组。"""
    provider = FakeProvider(_payload())  # 只有 requirements，没有 modules

    result = ExtractSkill(provider=provider).extract(RAW, source_type="web")

    assert result.modules == []
    assert result.requirements == ["支持快速打卡", "生成统计图表"]
