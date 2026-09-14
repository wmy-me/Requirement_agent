"""对话里「需要我提交吗？」的应答识别。

判定刻意收得很紧：误判会把一句普通聊天变成一条待办，代价比漏判大得多。
"""

from requirement_agent.api.routes.agent_chat import (
    _find_confirmable_analysis,
    _is_submit_confirmation,
)


def _assistant_asking(content: str, *, raw_text: str = "某段需求正文") -> dict[str, object]:
    return {
        "role": "assistant",
        "content": content,
        "artifacts": {"extracted": {"raw_text": raw_text}, "source_type": "web"},
    }


# ── 判定：该认的 ────────────────────────────────────────────────────────


def test_accepts_short_affirmatives() -> None:
    for text in ("需要", "好的", "可以", "确认", "是的", "行", "提交", "嗯", "OK", "需要。"):
        assert _is_submit_confirmation(text) is True, text


def test_accepts_sentence_that_mentions_submitting() -> None:
    """用户实际说过的那句：以肯定词开头，且明确提到提交/待办/审核。"""
    assert _is_submit_confirmation("需要你把这条正式提交进待办审核队列") is True
    assert _is_submit_confirmation("好的，请提交到待办") is True


# ── 判定：不该认的（误判代价更大）────────────────────────────────────────


def test_rejects_real_requirements_starting_with_affirmative_prefix() -> None:
    """以「需要」开头但是真需求——不能被当成确认。"""
    assert _is_submit_confirmation("需要支持按部门筛选导出") is False
    assert _is_submit_confirmation("需要把登录改成短信验证码") is False
    assert _is_submit_confirmation("好用的报表要能导出") is False


def test_rejects_long_or_empty_text() -> None:
    assert _is_submit_confirmation("") is False
    assert _is_submit_confirmation("   ") is False
    assert _is_submit_confirmation("需要" * 40) is False  # 超长


# ── 前置条件：上一轮助手必须真的问了「要不要提交」────────────────────────


def test_find_confirmable_requires_preceding_question() -> None:
    messages = [_assistant_asking("我已经把你的需求理解成……还需要补充哪些信息？")]

    assert _find_confirmable_analysis(messages) is None


def test_find_confirmable_requires_usable_text() -> None:
    messages = [_assistant_asking("需要我把这条正式提交进待办审核队列吗？", raw_text="")]

    assert _find_confirmable_analysis(messages) is None


def test_find_confirmable_returns_latest_qualifying_artifacts() -> None:
    older = _assistant_asking("需要我把这条正式提交进待办审核队列吗？", raw_text="旧的需求")
    newer = _assistant_asking("需要我把这条正式提交进待办审核队列吗？", raw_text="新的需求")
    messages = [older, {"role": "user", "content": "需要"}, newer]

    found = _find_confirmable_analysis(messages)

    assert found is not None
    assert found["extracted"]["raw_text"] == "新的需求"


def test_find_confirmable_ignores_missing_history() -> None:
    assert _find_confirmable_analysis([]) is None
