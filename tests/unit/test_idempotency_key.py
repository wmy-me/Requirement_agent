"""幂等键构造的回归测试。

背景：早先幂等键是 `{渠道}:{发起人}:{正文}` 直接拼接，正文一长就撑爆 Postgres
btree 索引行上限（约 2704 字节），INSERT 被拒：

    index row size 4424 exceeds btree version 4 maximum 2704

表现为「长文本需求怎么也存不进库、短文本却正常」，且错误信息完全指不到根因。
"""

from requirement_agent.domain.requirement import build_idempotency_key


def test_key_length_is_bounded_regardless_of_text() -> None:
    """这是本修复的核心：键长必须与正文长度无关。"""
    short = build_idempotency_key(source_type="web", requester="chat", text="短")
    long_text = build_idempotency_key(source_type="web", requester="chat", text="长" * 10000)

    assert len(short) == len(long_text)
    assert len(long_text) < 100  # 远低于 2704 的索引上限


def test_same_inputs_yield_same_key() -> None:
    first = build_idempotency_key(source_type="web", requester="chat", text="同一段需求")
    second = build_idempotency_key(source_type="web", requester="chat", text="同一段需求")

    assert first == second


def test_different_text_yields_different_key() -> None:
    first = build_idempotency_key(source_type="web", requester="chat", text="需求甲")
    second = build_idempotency_key(source_type="web", requester="chat", text="需求乙")

    assert first != second


def test_source_type_and_requester_partition_the_key() -> None:
    base = {"text": "同一段需求"}
    web = build_idempotency_key(source_type="web", requester="alice", **base)
    feishu = build_idempotency_key(source_type="feishu", requester="alice", **base)
    bob = build_idempotency_key(source_type="web", requester="bob", **base)

    assert len({web, feishu, bob}) == 3


def test_requester_falls_back_to_anonymous() -> None:
    none_key = build_idempotency_key(source_type="web", requester=None, text="x")
    blank_key = build_idempotency_key(source_type="web", requester="", text="x")

    assert none_key == blank_key
    assert ":anonymous:" in none_key


def test_key_is_prefixed_for_readability() -> None:
    """键仍以渠道与发起人开头，便于人工排查时一眼看出归属。"""
    key = build_idempotency_key(source_type="feishu", requester="ou_123", text="x")

    assert key.startswith("feishu:ou_123:")
