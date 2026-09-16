"""版本时刻的功能集复原（G 批 · G2）。

**这里要钉死的是一个此前从未被验证过的语义**：`features?at_version=N` 原先只复原
「当时哪些功能存在」，`content` 拿的是**当前值**（`modify` 是就地覆写，旧文字没另存）。
所以一个在 v2 被改过措辞的功能，查 v1 会返回 v2 之后的文字 —— 而 `/diff` 的左右两端
用的是同一个方法，`before != after` 因此**永远为假**，`modified` 结构上恒空。

历史用**裸 SQL 造**（而不是走审核流程）：这里要覆盖的是「记录已经长这样时能不能正确复原」，
包括真实写路径产不出来的畸形组合。真实写路径产出的记录形状由 `test_requirement_revert.py`
的端到端用例覆盖。

打真实库，用完自清理。
"""

from __future__ import annotations

import json
import os
import uuid

os.environ.setdefault("API_AUTH_TOKEN", "test-api-token")

from sqlalchemy import text

from requirement_agent.infrastructure.db.repositories import RequirementFeatureRepository
from requirement_agent.infrastructure.db.session import SessionLocal

feature_repo = RequirementFeatureRepository()


def _new_id() -> int:
    return uuid.uuid4().int % 9_000_000_000_000_000 + 1_000_000_000_000_000


class _HistoryFixture:
    """一条三版需求：

    - v1 `new` ：引入 F-001「支持导出 Excel」与 F-002「支持打印」
    - v2 `modify`：把 F-001 改成「支持导出 Excel 与 CSV」（**就地覆写，旧文字不另存**）
    - v3 `modify`：删掉 F-002，引入 F-003「支持定时导出」

    当下状态：F-001（内容已是 v2 的值）、F-002（已软删，removed_version_no=3）、F-003。
    这样每个版本都同时覆盖「内容变过」「删掉过」「后来才加」三种情况。
    """

    def __init__(self) -> None:
        self.tag = uuid.uuid4().hex[:8]
        self.master_id = _new_id()
        self.key = f"REQ-FH-{self.tag}"
        self.ids = {"F-001": _new_id(), "F-002": _new_id(), "F-003": _new_id()}
        v1_content = "支持导出 Excel"
        v2_content = "支持导出 Excel 与 CSV"
        self.expected = {
            1: {"F-001": v1_content, "F-002": "支持打印"},
            2: {"F-001": v2_content, "F-002": "支持打印"},
            3: {"F-001": v2_content, "F-003": "支持定时导出"},
        }
        with SessionLocal() as session:
            session.execute(
                text(
                    "INSERT INTO requirement_master (id, requirement_key, requirement_name, "
                    "final_requirement, current_version, status, lock_version) "
                    "VALUES (:id, :key, :name, 'x', 3, 'active', 3)"
                ),
                {"id": self.master_id, "key": self.key, "name": f"历史复原-{self.tag}"},
            )
            self._insert_features(session)
            self._insert_versions(session, v1_content, v2_content)
            session.commit()

    def _insert_features(self, session) -> None:
        rows = [
            # (key, content, status, ordinal, origin_version_no, removed_version_no, row_id)
            ("F-001", "支持导出 Excel 与 CSV", "active", 1, 1, None, self.ids["F-001"]),
            ("F-002", "支持打印", "deleted", 2, 1, 3, self.ids["F-002"]),
            ("F-003", "支持定时导出", "active", 3, 3, None, self.ids["F-003"]),
        ]
        for key, content, status, ordinal, origin, removed, row_id in rows:
            session.execute(
                text(
                    "INSERT INTO requirement_feature (id, requirement_id, feature_key, content, "
                    "status, ordinal, origin_source_id, origin_requirement_key, origin_version_no, "
                    "removed_version_no, provenance, content_hash) "
                    "VALUES (:id, :rid, :key, :content, :status, :ordinal, NULL, :rk, :origin, "
                    ":removed, CAST('[]' AS JSONB), 'h')"
                ),
                {
                    "id": row_id,
                    "rid": self.master_id,
                    "key": key,
                    "content": content,
                    "status": status,
                    "ordinal": ordinal,
                    "rk": self.key,
                    "origin": origin,
                    "removed": removed,
                },
            )

    def _insert_versions(self, session, v1_content: str, v2_content: str) -> None:
        versions = [
            (
                1,
                "new",
                [
                    {"op": "add", "feature_key": "F-001", "content": v1_content},
                    {"op": "add", "feature_key": "F-002", "content": "支持打印"},
                ],
            ),
            (
                2,
                "modify",
                [
                    {
                        "op": "modify",
                        "feature_key": "F-001",
                        "before": v1_content,
                        "after": v2_content,
                    }
                ],
            ),
            (
                3,
                "modify",
                [
                    {"op": "delete", "feature_key": "F-002", "content": "支持打印"},
                    {"op": "add", "feature_key": "F-003", "content": "支持定时导出"},
                ],
            ),
        ]
        for version_no, change_type, changes in versions:
            session.execute(
                text(
                    "INSERT INTO requirement_version (id, requirement_id, version_no, version_title, "
                    "change_type, requirement_snapshot, change_summary, feature_changes, created_by, "
                    "reviewed_by, status, capability_snapshot, constraint_snapshot) "
                    "VALUES (:id, :rid, :vno, 't', :ct, 's', 'c', CAST(:fc AS JSONB), 'a', 'a', "
                    ":st, CAST('[]' AS JSONB), CAST('[]' AS JSONB))"
                ),
                {
                    "id": _new_id(),
                    "rid": self.master_id,
                    "vno": version_no,
                    "ct": change_type,
                    "fc": json.dumps(changes),
                    "st": "current" if version_no == 3 else "superseded",
                },
            )

    def rows_at(self, at_version: int) -> list[dict[str, object]]:
        return feature_repo.list_by_requirement_key(self.key, at_version=at_version)

    def cleanup(self) -> None:
        with SessionLocal() as session:
            session.execute(
                text("DELETE FROM requirement_version WHERE requirement_id = :id"),
                {"id": self.master_id},
            )
            session.execute(
                text("DELETE FROM requirement_feature WHERE requirement_id = :id"),
                {"id": self.master_id},
            )
            session.execute(text("DELETE FROM requirement_master WHERE id = :id"), {"id": self.master_id})
            session.commit()


def _contents(rows: list[dict[str, object]]) -> dict[str, str]:
    return {str(row["feature_key"]): str(row["content"]) for row in rows}


# ── 成员与内容都要复原 ────────────────────────────────────────────────────


def test_each_version_returns_its_own_members_and_contents() -> None:
    fixture = _HistoryFixture()
    try:
        for at_version, expected in fixture.expected.items():
            assert _contents(fixture.rows_at(at_version)) == expected, f"v{at_version} 复原不符"
    finally:
        fixture.cleanup()


def test_modified_feature_returns_the_text_of_that_version() -> None:
    """这是本次修复的核心：F-001 的当前内容是 v2 的值，查 v1 必须回到 v1 的文字。"""
    fixture = _HistoryFixture()
    try:
        assert _contents(fixture.rows_at(1))["F-001"] == "支持导出 Excel"
        assert _contents(fixture.rows_at(3))["F-001"] == "支持导出 Excel 与 CSV"
    finally:
        fixture.cleanup()


def test_deleted_feature_is_alive_again_in_earlier_versions() -> None:
    fixture = _HistoryFixture()
    try:
        assert "F-002" in _contents(fixture.rows_at(2))
        assert "F-002" not in _contents(fixture.rows_at(3))
        # 复原出来的存活行，状态字段要与「当时还活着」自洽
        row = next(item for item in fixture.rows_at(2) if item["feature_key"] == "F-002")
        assert (row["status"], row["removed_version_no"]) == ("active", None)
    finally:
        fixture.cleanup()


def test_target_version_equal_to_current_matches_current_view() -> None:
    """不变式：at_version = 当前版本时，结果与不带 at_version 的默认视图等价。"""
    fixture = _HistoryFixture()
    try:
        assert _contents(fixture.rows_at(3)) == _contents(
            feature_repo.list_by_requirement_key(fixture.key)
        )
    finally:
        fixture.cleanup()


def test_output_shape_is_unchanged() -> None:
    """契约安全：走 at_version 时字段集必须与不走时**逐字段一致**（前端不认得新形状）。"""
    fixture = _HistoryFixture()
    try:
        default_keys = set(feature_repo.list_by_requirement_key(fixture.key)[0].keys())
        assert set(fixture.rows_at(1)[0].keys()) == default_keys
    finally:
        fixture.cleanup()


def test_ordering_follows_ordinal() -> None:
    fixture = _HistoryFixture()
    try:
        assert [row["feature_key"] for row in fixture.rows_at(1)] == ["F-001", "F-002"]
    finally:
        fixture.cleanup()


# ── 连带修复：/diff 的 modified ──────────────────────────────────────────


def test_diff_reports_modified_between_versions() -> None:
    """**回归**：修复前 `modified` 结构上恒空（两端取同一条当前值）。"""
    fixture = _HistoryFixture()
    try:
        diff = feature_repo.diff_by_requirement_key(fixture.key, from_version=1, to_version=2)

        assert diff["modified"] == [
            {
                "feature_key": "F-001",
                "before": "支持导出 Excel",
                "after": "支持导出 Excel 与 CSV",
            }
        ]
        assert diff["added"] == []
        assert diff["removed"] == []
        assert diff["unchanged"] == 1  # F-002 两版都没变
    finally:
        fixture.cleanup()


def test_diff_reports_added_and_removed_across_three_versions() -> None:
    fixture = _HistoryFixture()
    try:
        diff = feature_repo.diff_by_requirement_key(fixture.key, from_version=1, to_version=3)

        assert [row["feature_key"] for row in diff["added"]] == ["F-003"]
        assert [row["feature_key"] for row in diff["removed"]] == ["F-002"]
        assert [row["feature_key"] for row in diff["modified"]] == ["F-001"]
        assert diff["unchanged"] == 0
    finally:
        fixture.cleanup()


def test_diff_rows_keep_the_full_feature_shape() -> None:
    """`added`/`removed` 是完整 feature 行（含 module_key/module_name），形状不能变。"""
    fixture = _HistoryFixture()
    try:
        default_keys = set(feature_repo.list_by_requirement_key(fixture.key)[0].keys())
        diff = feature_repo.diff_by_requirement_key(fixture.key, from_version=1, to_version=3)

        assert set(diff["added"][0].keys()) == default_keys
        assert set(diff["removed"][0].keys()) == default_keys
    finally:
        fixture.cleanup()


# ── 回归：不带 at_version 的路径一个字节都不能变 ──────────────────────────


def test_default_view_is_untouched_by_the_rewrite() -> None:
    """最要紧的一条回归：默认路径（详情页在用）行为与改动前完全一致。"""
    fixture = _HistoryFixture()
    try:
        rows = feature_repo.list_by_requirement_key(fixture.key)
        assert _contents(rows) == {
            "F-001": "支持导出 Excel 与 CSV",
            "F-003": "支持定时导出",
        }
        # 已删行不出现在默认视图里
        assert "F-002" not in _contents(rows)
    finally:
        fixture.cleanup()


def test_include_deleted_still_wins_over_at_version() -> None:
    """既有行为：同时给 include_deleted 与 at_version 时，前者顶掉后者（原样保留）。"""
    fixture = _HistoryFixture()
    try:
        rows = feature_repo.list_by_requirement_key(fixture.key, at_version=1, include_deleted=True)
        assert set(_contents(rows)) == {"F-001", "F-002", "F-003"}
    finally:
        fixture.cleanup()
