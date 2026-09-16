"""功能行同步的 diff 内核。

这个文件首先是**旧缺陷的回归测试**：`sync_features` 曾用 ordinal 位置当身份，
往中间插入一行会让其后每一行都被判成 `modify` 并真实覆写内容。下面第一条用例
（`test_insert_in_middle_is_a_single_add`）在旧实现下会得到「4 条 modify + 1 条 add」，
在新内核下必须是「1 条 add + 5 条 keep」。

其余用例钉住两处容易搞错的规则：
- 模块标签的合并**不对称**（新行没模块 ≠ 清空模块）；
- 匹配上的行**保持原 ordinal**，新行追加到末尾（不整体重排目标 REQ）。
"""

from __future__ import annotations

from requirement_agent.domain.feature_diff import (
    FeatureRow,
    normalize_feature_rows,
    plan_sync,
)


def _existing(*specs) -> list[dict[str, object]]:
    """按顺序造 `list_active` 形状的现有行。

    元素可以是 `"内容"`（无模块）或 `("内容", "模块")`。
    """
    rows: list[dict[str, object]] = []
    for ordinal, spec in enumerate(specs, start=1):
        content, module = spec if isinstance(spec, tuple) else (spec, None)
        rows.append(
            {
                "id": ordinal * 100,
                "feature_key": f"F-{ordinal:03d}",
                "content": content,
                "ordinal": ordinal,
                "module_key": module,
                "module_name": module,
            }
        )
    return rows


def _incoming(*specs) -> list:
    """来源功能行：`"内容"` 或 `dict(content=..., module_key=...)`。"""
    return list(specs)


def _ops(plan) -> list[str]:
    return [item.op for item in plan]


def _by_content(plan, content: str):
    return next(item for item in plan if item.content == content)


# ── 旧缺陷的回归 ────────────────────────────────────────────────────────


def test_insert_in_middle_is_a_single_add() -> None:
    """中间插入一行，只应产生 1 条 add，其余全部 keep。

    旧实现（ordinal 配对）会把它变成 4 条 modify + 1 条 add，并覆写 B/C/D/E 的内容。
    """
    existing = _existing("A", "B", "C", "D", "E")
    plan = plan_sync(existing, normalize_feature_rows(_incoming("A", "X", "B", "C", "D", "E")))

    assert _ops(plan).count("add") == 1
    assert _ops(plan).count("modify") == 0
    assert _ops(plan).count("keep") == 5

    # 既有行一个都没被改写
    for content in ("A", "B", "C", "D", "E"):
        assert _by_content(plan, content).op == "keep"
    assert _by_content(plan, "X").op == "add"


def test_insert_in_middle_keeps_original_ordinals() -> None:
    """既有行保持原 ordinal，新行追加到末尾 —— 不重排目标 REQ 的版式。"""
    existing = _existing("A", "B", "C", "D", "E")
    plan = plan_sync(existing, normalize_feature_rows(_incoming("A", "X", "B", "C", "D", "E")))

    assert _by_content(plan, "A").ordinal == 1
    assert _by_content(plan, "B").ordinal == 2
    assert _by_content(plan, "C").ordinal == 3
    assert _by_content(plan, "D").ordinal == 4
    assert _by_content(plan, "E").ordinal == 5
    assert _by_content(plan, "X").ordinal == 6


def test_delete_in_middle_is_a_single_delete() -> None:
    existing = _existing("A", "B", "C", "D")
    plan = plan_sync(existing, normalize_feature_rows(_incoming("A", "C", "D")), prune=True)

    assert _ops(plan).count("delete") == 1
    assert _ops(plan).count("modify") == 0
    deleted = [item for item in plan if item.op == "delete"][0]
    assert deleted.content == "B"
    assert deleted.feature_key == "F-002"


# ── prune 语义 ──────────────────────────────────────────────────────────


def test_union_keeps_unmatched_existing_rows() -> None:
    """prune=False（合并默认）：来源没提到的现有功能保留，合并是并集。"""
    existing = _existing("A", "B", "C")
    plan = plan_sync(existing, normalize_feature_rows(_incoming("A")))

    assert _ops(plan).count("delete") == 0
    assert _ops(plan).count("keep") == 3
    assert _by_content(plan, "B").ordinal == 2
    assert _by_content(plan, "C").ordinal == 3


def test_prune_removes_unmatched_existing_rows() -> None:
    """prune=True（回滚用）：以来源为准整体替换。"""
    existing = _existing("A", "B", "C")
    plan = plan_sync(existing, normalize_feature_rows(_incoming("A")), prune=True)

    assert _ops(plan).count("delete") == 2
    assert {item.content for item in plan if item.op == "delete"} == {"B", "C"}


def test_short_source_does_not_wipe_long_target_under_union() -> None:
    """3 行的来源并进 10 行的目标：并集语义下目标一条都不能少。

    这正是旧实现最危险的行为 —— 它按「位置超出末尾」删除，会把目标删剩 3 行。
    新内核下 3 行在同模块内按序配成 modify、其余 7 行原样保留，**删除数必须为 0**。
    """
    existing = _existing(*[f"旧{i}" for i in range(1, 11)])
    plan = plan_sync(existing, normalize_feature_rows(_incoming("新1", "新2", "新3")))

    assert _ops(plan).count("delete") == 0
    assert _ops(plan).count("modify") == 3
    assert _ops(plan).count("keep") == 7
    # 被改写的行留下了原文，没有静默丢失
    assert {item.before for item in plan if item.op == "modify"} == {"旧1", "旧2", "旧3"}


def test_paired_match_does_not_cascade_past_unchanged_rows() -> None:
    """第 3 遍只在真正改动的行之间配对，不会把后面的未变行也拖成 modify。

    这是与旧实现最关键的区别：旧实现里「改一行」会让它后面每一行都变 modify。
    """
    existing = _existing("A", "B改前", "C", "D")
    plan = plan_sync(existing, normalize_feature_rows(_incoming("A", "B改后", "C", "D")))

    assert _ops(plan) == ["keep", "modify", "keep", "keep"]
    assert _by_content(plan, "B改后").match_kind == "paired"


def test_paired_match_requires_same_module() -> None:
    """跨模块的「内容全变了」不配对，否则会把某个模块的功能误改成另一个模块的。"""
    existing = _existing(("旧甲", "模块A"))
    plan = plan_sync(
        existing, normalize_feature_rows(_incoming({"content": "新乙", "module_key": "模块B"}))
    )

    assert sorted(_ops(plan)) == ["add", "keep"]
    assert _by_content(plan, "旧甲").module_key == "模块A"


# ── 模块标签的合并规则（不对称）────────────────────────────────────────


def test_module_rename_is_a_match_not_delete_plus_add() -> None:
    """模块改名走第 2 遍跨模块匹配，不能退化成 delete + add（否则 feature_key 断裂）。"""
    existing = _existing(("登录", "登录"))
    plan = plan_sync(
        existing, normalize_feature_rows(_incoming({"content": "登录", "module_key": "用户登录"}))
    )

    assert _ops(plan) == ["keep"]
    row = plan[0]
    assert row.match_kind == "content"
    assert (row.module_key, row.module_name) == ("用户登录", "用户登录")
    assert row.module_before == ("登录", "登录")


def test_incoming_without_module_keeps_existing_module() -> None:
    """新行没有模块 ≠ 清空模块 —— 缺信息不是信息。"""
    existing = _existing(("登录", "鉴权"))
    plan = plan_sync(existing, normalize_feature_rows(_incoming("登录")))

    assert _ops(plan) == ["keep"]
    row = plan[0]
    assert (row.module_key, row.module_name) == ("鉴权", "鉴权")
    assert row.module_before is None


def test_flat_source_does_not_strip_module_labels() -> None:
    """把一条扁平来源并进带模块的 REQ：12 条标签一条都不能被抹掉。"""
    existing = _existing(*[(f"功能{i}", f"模块{i}") for i in range(1, 13)])
    plan = plan_sync(existing, normalize_feature_rows(_incoming("全新功能")))

    kept = [item for item in plan if item.op == "keep"]
    assert len(kept) == 12
    assert all(item.module_key is not None for item in kept)


def test_incoming_module_labels_unlabeled_existing_row() -> None:
    existing = _existing("登录")
    plan = plan_sync(
        existing, normalize_feature_rows(_incoming({"content": "登录", "module_key": "鉴权"}))
    )

    row = plan[0]
    assert row.op == "keep"
    assert (row.module_key, row.module_name) == ("鉴权", "鉴权")
    assert row.module_before == (None, None)


def test_same_module_match_is_labelled_module() -> None:
    existing = _existing(("登录", "鉴权"))
    plan = plan_sync(
        existing, normalize_feature_rows(_incoming({"content": "登录", "module_key": "鉴权"}))
    )

    assert plan[0].match_kind == "module"
    assert plan[0].module_before is None


# ── 内容变更 ────────────────────────────────────────────────────────────


def test_content_change_is_a_modify_with_before() -> None:
    existing = _existing("旧文案")
    plan = plan_sync(existing, normalize_feature_rows(_incoming("新文案")))

    assert _ops(plan) == ["modify"]
    assert plan[0].before == "旧文案"
    assert plan[0].feature_key == "F-001"


def test_module_change_alone_does_not_emit_a_degenerate_modify() -> None:
    """模块变了但内容没变：op 仍是 keep，避免版本记录里出现 before == after 的 modify。"""
    existing = _existing(("登录", "登录"))
    plan = plan_sync(
        existing, normalize_feature_rows(_incoming({"content": "登录", "module_key": "鉴权"}))
    )

    assert _ops(plan) == ["keep"]
    assert plan[0].module_before == ("登录", "登录")


# ── 重复内容（多重集匹配）──────────────────────────────────────────────


def test_duplicate_content_rows_match_one_to_one() -> None:
    existing = _existing("A", "A")
    plan = plan_sync(existing, normalize_feature_rows(_incoming("A", "A")))

    assert _ops(plan) == ["keep", "keep"]


def test_extra_duplicate_row_becomes_an_add() -> None:
    existing = _existing("A")
    plan = plan_sync(existing, normalize_feature_rows(_incoming("A", "A")))

    assert _ops(plan) == ["keep", "add"]


# ── 边界 ────────────────────────────────────────────────────────────────


def test_empty_existing_makes_everything_add_from_one() -> None:
    plan = plan_sync([], normalize_feature_rows(_incoming("A", "B")))

    assert _ops(plan) == ["add", "add"]
    assert [item.ordinal for item in plan] == [1, 2]


def test_empty_incoming_keeps_everything_under_union() -> None:
    plan = plan_sync(_existing("A", "B"), [])

    assert _ops(plan) == ["keep", "keep"]


def test_content_hash_is_recomputed_ignoring_stale_column() -> None:
    """历史行的 `content_hash` 可能是 NULL 或脏值，内核必须从 content 现算。"""
    existing = _existing("登录")
    existing[0]["content_hash"] = None  # 列里是 NULL
    plan = plan_sync(existing, normalize_feature_rows(_incoming("登录")))

    assert _ops(plan) == ["keep"]


# ── normalize_feature_rows ──────────────────────────────────────────────


def test_normalize_accepts_strings_dicts_and_mixes() -> None:
    rows = normalize_feature_rows(
        ["  纯文本  ", {"content": "带模块", "module_key": "鉴权", "module_name": "鉴权"}, "   "]
    )

    assert [row.content for row in rows] == ["纯文本", "带模块"]
    assert rows[0].module_key is None
    assert rows[1].module_key == "鉴权"


def test_normalize_falls_back_to_key_when_name_missing() -> None:
    rows = normalize_feature_rows([{"content": "x", "module_key": "鉴权"}])

    assert rows[0].module_name == "鉴权"


def test_normalize_drops_blank_rows_and_trims() -> None:
    rows = normalize_feature_rows(["", "   ", {"content": "  "}, "有效"])

    assert [row.content for row in rows] == ["有效"]


def test_feature_row_hash_matches_sha256_of_content() -> None:
    import hashlib

    row = FeatureRow(content="登录")

    assert row.content_hash == hashlib.sha256("登录".encode("utf-8")).hexdigest()
