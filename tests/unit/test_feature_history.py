"""`domain/feature_history` 的边界表（纯函数，不建库）。

这一批的重点是**逆放的边界**：改多次、删了又回滚、目标之后才新增、模块随 modify 复原、
以及两类**不可复原**的模块变化（`apply_overrides` 的 modify 不记模块、模块纯漂移走 keep
不产出记录）—— 后者是已知限制，用测试把它钉死，免得后来人以为是 bug。
"""

from __future__ import annotations

from requirement_agent.domain.feature_history import (
    FeatureState,
    plan_revert,
    reconstruct_features_at_version,
)


def _row(
    key: str,
    content: str,
    *,
    ordinal: int = 1,
    module_key: str | None = None,
    module_name: str | None = None,
    status: str = "active",
    removed_version_no: int | None = None,
    row_id: int = 1,
) -> dict[str, object]:
    return {
        "id": row_id,
        "feature_key": key,
        "content": content,
        "status": status,
        "ordinal": ordinal,
        "module_key": module_key,
        "module_name": module_name,
        "removed_version_no": removed_version_no,
    }


def _version(version_no: int, *changes: dict[str, object]) -> dict[str, object]:
    return {"version_no": version_no, "feature_changes": list(changes)}


def _add(key: str, content: str) -> dict[str, object]:
    return {"op": "add", "feature_key": key, "content": content}


def _modify(key: str, before: str, after: str, **extra: object) -> dict[str, object]:
    return {"op": "modify", "feature_key": key, "before": before, "after": after, **extra}


def _delete(key: str, content: str) -> dict[str, object]:
    return {"op": "delete", "feature_key": key, "content": content}


def _by_key(states: list[FeatureState]) -> dict[str, FeatureState]:
    return {item.feature_key: item for item in states}


# --------------------------------------------------------------------------
# 逆放规则
# --------------------------------------------------------------------------


def test_modify_chain_replays_backwards() -> None:
    """同一 feature 被改多次：目标落在链首/中间时都要取到当时的内容。"""
    rows = [_row("F-001", "第三版文字")]
    history = [
        _version(2, _modify("F-001", "第一版文字", "第二版文字")),
        _version(3, _modify("F-001", "第二版文字", "第三版文字")),
    ]

    assert _by_key(reconstruct_features_at_version(rows, history, target_version=1))["F-001"].content == "第一版文字"
    assert _by_key(reconstruct_features_at_version(rows, history, target_version=2))["F-001"].content == "第二版文字"
    assert _by_key(reconstruct_features_at_version(rows, history, target_version=3))["F-001"].content == "第三版文字"


def test_feature_added_after_target_is_excluded() -> None:
    """目标版本之后才 add 的 key，不该出现在目标时刻。"""
    rows = [_row("F-001", "一直在", ordinal=1), _row("F-002", "后来才有", ordinal=2, row_id=2)]
    history = [_version(2, _add("F-002", "后来才有"))]

    at_v1 = reconstruct_features_at_version(rows, history, target_version=1)
    assert [item.feature_key for item in at_v1] == ["F-001"]
    assert [item.feature_key for item in reconstruct_features_at_version(rows, history, target_version=2)] == [
        "F-001",
        "F-002",
    ]


def test_removed_feature_is_restored_from_delete_record() -> None:
    """目标版本之后被删的行要复活，内容取 delete 记录里的值。"""
    rows = [_row("F-001", "还在", ordinal=1), _row("F-002", "已被删", status="deleted", ordinal=2, removed_version_no=3, row_id=2)]
    history = [_version(3, _delete("F-002", "已被删"))]

    at_v2 = reconstruct_features_at_version(rows, history, target_version=2)
    assert [item.feature_key for item in at_v2] == ["F-001", "F-002"]
    assert _by_key(at_v2)["F-002"].content == "已被删"


def test_deleted_then_revived_is_not_alive_in_the_gap() -> None:
    """删除后又被回滚复活：中间那几版应该「不存在」。"""
    rows = [_row("F-001", "复活后的文字", ordinal=1)]
    history = [
        _version(3, _delete("F-001", "删除时的文字")),
        _version(5, _add("F-001", "复活后的文字")),
    ]

    assert [item.feature_key for item in reconstruct_features_at_version(rows, history, target_version=2)] == ["F-001"]
    assert reconstruct_features_at_version(rows, history, target_version=4) == []
    # v5 之后（含当前）又是存在的
    assert [item.feature_key for item in reconstruct_features_at_version(rows, history, target_version=5)] == ["F-001"]


def test_module_restored_from_modify_record() -> None:
    """modify 记录带 module_before 时，模块一并回到当时的值（name 退化为 key）。"""
    rows = [_row("F-001", "新文字", module_key="新模块", module_name="新模块")]
    history = [
        _version(2, _modify("F-001", "旧文字", "新文字", module_before="旧模块", module_after="新模块")),
    ]

    restored = _by_key(reconstruct_features_at_version(rows, history, target_version=1))["F-001"]
    assert (restored.content, restored.module_key, restored.module_name) == ("旧文字", "旧模块", "旧模块")


def test_module_drift_without_record_keeps_current_value() -> None:
    """**已知限制**：模块变了但内容没变时写入侧走 keep、不产出记录 → 不可复原。"""
    rows = [_row("F-001", "没变过", module_key="新模块", module_name="新模块")]
    history = [_version(2)]  # 该版本只有 keep，没有 feature_changes

    restored = _by_key(reconstruct_features_at_version(rows, history, target_version=1))["F-001"]
    assert restored.module_key == "新模块"  # 保持当前值，而不是当时的值


def test_target_version_at_or_above_current_is_identity() -> None:
    """目标版本 == 当前版本时，结果恒等于「当前生效集合」（不变式）。"""
    rows = [
        _row("F-002", "B", ordinal=1, row_id=2),
        _row("F-001", "A", ordinal=2, row_id=1),
        _row("F-009", "已删", status="deleted", ordinal=3, removed_version_no=4, row_id=9),
    ]

    assert [item.feature_key for item in reconstruct_features_at_version(rows, [], target_version=5)] == ["F-002", "F-001"]


def test_ordering_is_stable_by_ordinal_then_key() -> None:
    rows = [
        _row("F-009", "同序号", ordinal=3),
        _row("F-002", "同序号", ordinal=3, row_id=2),
        _row("F-001", "最前", ordinal=1, row_id=3),
    ]
    assert [item.feature_key for item in reconstruct_features_at_version(rows, [], target_version=1)] == [
        "F-001",
        "F-002",
        "F-009",
    ]


# --------------------------------------------------------------------------
# 防御：畸形数据不炸
# --------------------------------------------------------------------------


def test_unknown_key_in_history_does_not_crash() -> None:
    """记录指向表里没有的 key（软删时代不该发生）→ 不抛异常。"""
    rows = [_row("F-001", "正常")]
    history = [_version(2, _modify("F-999", "幽灵", "更幽灵"))]

    assert [item.feature_key for item in reconstruct_features_at_version(rows, history, target_version=1)] == ["F-001"]


def test_missing_before_keeps_content() -> None:
    """modify 记录缺 before → 内容不动（防御）。"""
    rows = [_row("F-001", "现状")]
    history = [_version(2, {"op": "modify", "feature_key": "F-001", "after": "现状"})]

    assert _by_key(reconstruct_features_at_version(rows, history, target_version=1))["F-001"].content == "现状"


def test_unknown_op_and_malformed_entries_are_ignored() -> None:
    rows = [_row("F-001", "现状")]
    history = [_version(2, {"op": "explode", "feature_key": "F-001"}, {}, {"op": "add"})]  # type: ignore[arg-type]

    assert _by_key(reconstruct_features_at_version(rows, history, target_version=1))["F-001"].content == "现状"


def test_same_version_changes_are_replayed_in_reverse() -> None:
    """同一版本内两条记录 → 逆序撤销（一条 feature_changes 是顺序施加的）。"""
    rows = [_row("F-001", "末尾")]
    history = [
        _version(
            2,
            _modify("F-001", "开头", "中间"),
            _modify("F-001", "中间", "末尾"),
        )
    ]

    assert _by_key(reconstruct_features_at_version(rows, history, target_version=1))["F-001"].content == "开头"


# --------------------------------------------------------------------------
# plan_revert：按 feature_key 对齐的归宿
# --------------------------------------------------------------------------


def test_plan_revert_ops() -> None:
    rows = [
        _row("F-001", "内容被改过", ordinal=1),
        _row("F-002", "要删掉的", ordinal=2, row_id=2),
        _row("F-003", "已软删待复活", status="deleted", ordinal=3, removed_version_no=4, row_id=3),
        _row("F-004", "原样保留", ordinal=4, row_id=4),
    ]
    target = [
        FeatureState("F-001", "原来的内容", 1),
        FeatureState("F-003", "复活后的内容", 3),
        FeatureState("F-004", "原样保留", 4),
    ]

    planned = {item.feature_key: item for item in plan_revert(rows, target)}

    assert planned["F-001"].op == "modify"
    assert (planned["F-001"].before, planned["F-001"].content) == ("内容被改过", "原来的内容")
    assert planned["F-003"].op == "add" and planned["F-003"].revived is True
    assert planned["F-003"].row_id == 3
    assert planned["F-004"].op == "keep"
    assert planned["F-002"].op == "delete"


def test_plan_revert_marks_missing_row_as_insert() -> None:
    """表里没有该 key → row_id 为 None（写库侧走 INSERT），且不算复活。"""
    planned = {item.feature_key: item for item in plan_revert([], [FeatureState("F-001", "新的", 1)])}

    assert planned["F-001"].op == "add"
    assert planned["F-001"].row_id is None
    assert planned["F-001"].revived is False


def test_plan_revert_reports_module_change_on_keep() -> None:
    """内容没变但模块变了 → 仍是 keep，改由 module_before 表达（与 plan_sync 同一约定）。"""
    rows = [_row("F-001", "没变", module_key="新模块", module_name="新模块")]
    target = [FeatureState("F-001", "没变", 1, module_key="旧模块", module_name="旧模块")]

    planned = plan_revert(rows, target)[0]
    assert planned.op == "keep"
    assert planned.module_before == ("新模块", "新模块")


def test_plan_revert_is_empty_when_target_equals_current() -> None:
    """目标与当前完全一致 → 一条变更都不产出（调用方据此 409，不产垃圾版本）。"""
    rows = [_row("F-001", "一样", ordinal=1), _row("F-002", "也一样", ordinal=2, row_id=2)]
    target = [FeatureState("F-001", "一样", 1), FeatureState("F-002", "也一样", 2)]

    planned = plan_revert(rows, target)
    assert {item.op for item in planned} == {"keep"}
