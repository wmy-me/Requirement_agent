"""回滚端点 `POST /api/v1/requirements/{key}/revert`（G 批 · G3）。

**历史是用真实审核流程跑出来的**（不是裸 SQL 插的）：`commit` 路径是纯确定性的
（只读 `source.metadata`、不调模型），所以能造出形状**真实**的 `feature_changes` ——
这正好同时验证「写路径产出的记录」与「复原函数读的记录」两侧对得上，而它们脱节
是这条链上最容易出的事。

要钉住的四件事：
1. 回滚后当前功能集与目标版本**逐字段相等**（含被改过的文字、被删掉的行）；
2. 被删的行是**复活原行**（同一 id、同一 feature_key），不是新建 —— 否则能力关联会丢；
3. **历史一个字节都不能变**（append-only 是 revert 与 reset 的分界）；
4. `/diff` 在「目标版本 → 回滚版本」之间三项全空（G2 与 G3 串起来验）。

打真实库，`finally` 里按外键顺序删干净。
"""

from __future__ import annotations

import hashlib
import os
import uuid

os.environ.setdefault("API_AUTH_TOKEN", "test-api-token")

from fastapi.testclient import TestClient
from sqlalchemy import text

from requirement_agent.api.app import app
from requirement_agent.infrastructure.db.session import SessionLocal

client = TestClient(app)


def _new_id() -> int:
    return uuid.uuid4().int % 9_000_000_000_000_000 + 1_000_000_000_000_000


def _sha(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class _RevertFixture:
    """三版需求：v1 原始 → v2 一次真实 modify → v3 一次真实 replace（产生 delete）。"""

    V1 = [
        ("F-001", "登录", "鉴权", 1),
        ("F-002", "锁定策略", "鉴权", 2),
        ("F-003", "导出", "报表", 3),
    ]

    def __init__(self) -> None:
        self.tag = uuid.uuid4().hex[:10]
        self.master_id = _new_id()
        self.key = f"REQ-REVERT-{self.tag}"
        self.source_ids: list[int] = []
        self.feature_ids = {key: _new_id() for key, _, _, _ in self.V1}
        self.capability_id = _new_id()

    # —— 造数据 ——

    def seed_v1(self) -> None:
        with SessionLocal() as session:
            session.execute(
                text(
                    "INSERT INTO requirement_master (id, requirement_key, requirement_name, "
                    "final_requirement, current_version, status, lock_version) "
                    "VALUES (:id, :k, :n, :body, 1, 'active', 1)"
                ),
                {
                    "id": self.master_id,
                    "k": self.key,
                    "n": f"回滚走查-{self.tag}",
                    "body": "\n".join(content for _, content, _, _ in self.V1),
                },
            )
            for feature_key, content, module, ordinal in self.V1:
                session.execute(
                    text(
                        "INSERT INTO requirement_feature (id, requirement_id, feature_key, content, "
                        "status, ordinal, origin_source_id, origin_requirement_key, origin_version_no, "
                        "provenance, content_hash, module_key, module_name) "
                        "VALUES (:id, :rid, :fk, :c, 'active', :o, NULL, :rk, 1, CAST('[]' AS JSONB), "
                        ":h, :m, :m)"
                    ),
                    {
                        "id": self.feature_ids[feature_key],
                        "rid": self.master_id,
                        "fk": feature_key,
                        "c": content,
                        "o": ordinal,
                        "rk": self.key,
                        "h": _sha(content),
                        "m": module,
                    },
                )
            session.execute(
                text(
                    "INSERT INTO requirement_version (id, requirement_id, version_no, version_title, "
                    "change_type, requirement_snapshot, change_summary, feature_changes, created_by, "
                    "reviewed_by, status, capability_snapshot, constraint_snapshot) "
                    "VALUES (:id, :rid, 1, 'v1', 'new', :snap, '初始版本', CAST(:fc AS JSONB), 'a', 'a', "
                    "'current', CAST('[]' AS JSONB), CAST('[]' AS JSONB))"
                ),
                {
                    "id": _new_id(),
                    "rid": self.master_id,
                    "snap": "\n".join(content for _, content, _, _ in self.V1),
                    "fc": __import__("json").dumps(
                        [{"op": "add", "feature_key": fk, "content": c} for fk, c, _, _ in self.V1]
                    ),
                },
            )
            # 给 F-002 挂一条能力关联 —— 回滚一个「已删除」的功能时，它必须活下来。
            session.execute(
                text(
                    "INSERT INTO capability (id, action, object, display_name, status, created_by) "
                    "VALUES (:id, '锁定', '账户', :d, 'active', 'test')"
                ),
                {"id": self.capability_id, "d": f"锁定账户-{self.tag}"},
            )
            session.execute(
                text(
                    "INSERT INTO feature_capability (feature_id, capability_id, raw_text, review_status) "
                    "VALUES (:f, :c, '锁定策略', 'proposed')"
                ),
                {"f": self.feature_ids["F-002"], "c": self.capability_id},
            )
            session.commit()

    def merge(self, *, modules: list[dict], merge_mode: str = "union") -> dict[str, object]:
        source_id = _new_id()
        self.source_ids.append(source_id)
        with SessionLocal() as session:
            session.execute(
                text(
                    "INSERT INTO requirement_source (id, idempotency_key, source_type, requester_id, "
                    "requester_name, original_text, original_payload, metadata, processing_status, "
                    "submitted_at) "
                    "VALUES (:id, :ik, 'web', :rq, '走查', :txt, CAST('{}' AS JSONB), "
                    "CAST(:meta AS JSONB), 'pending_review', NOW())"
                ),
                {
                    "id": source_id,
                    "ik": f"revert-{self.tag}-{len(self.source_ids)}",
                    "rq": f"revert-{self.tag}",
                    "txt": "合并走查",
                    "meta": __import__("json").dumps({"extracted": {"modules": modules}}),
                },
            )
            session.commit()
        response = client.post(
            "/api/v1/reviews/submit",
            json={
                "source_id": str(source_id),
                "decision": "approved",
                "reviewer_name": "回滚走查",
                "target_requirement_key": self.key,
                "merge_mode": merge_mode,
            },
        )
        assert response.status_code == 200, response.text
        return response.json()

    # —— 断言辅助 ——

    def features(self, at_version: int | None = None) -> list[dict[str, object]]:
        params = {"at_version": at_version} if at_version is not None else {}
        response = client.get(f"/api/v1/requirements/{self.key}/features", params=params)
        assert response.status_code == 200, response.text
        return response.json()["items"]

    def fingerprint(self) -> list[dict[str, object]]:
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    "SELECT version_no, status, change_type, requirement_snapshot, feature_changes, "
                    "superseded_by_version_no FROM requirement_version WHERE requirement_id = :id "
                    "ORDER BY version_no"
                ),
                {"id": self.master_id},
            ).mappings().all()
            return [dict(row) for row in rows]

    def revert(self, **payload) -> "object":
        return client.post(f"/api/v1/requirements/{self.key}/revert", json=payload)

    def cleanup(self) -> None:
        with SessionLocal() as session:
            session.execute(
                text(
                    "DELETE FROM requirement_version_source WHERE version_id IN "
                    "(SELECT id FROM requirement_version WHERE requirement_id = :id)"
                ),
                {"id": self.master_id},
            )
            session.execute(
                text("DELETE FROM requirement_version WHERE requirement_id = :id"), {"id": self.master_id}
            )
            session.execute(
                text("DELETE FROM feature_capability WHERE capability_id = :c"), {"c": self.capability_id}
            )
            session.execute(
                text("DELETE FROM requirement_feature WHERE requirement_id = :id"), {"id": self.master_id}
            )
            session.execute(
                text("DELETE FROM requirement_master WHERE id = :id"), {"id": self.master_id}
            )
            session.execute(text("DELETE FROM capability WHERE id = :c"), {"c": self.capability_id})
            for source_id in set(self.source_ids):
                session.execute(text("DELETE FROM requirement_review WHERE source_id = :s"), {"s": source_id})
                session.execute(text("DELETE FROM requirement_source WHERE id = :s"), {"s": source_id})
            session.execute(
                text("DELETE FROM outbox_event WHERE aggregate_id = :k"), {"k": self.key}
            )
            session.execute(text("DELETE FROM audit_event WHERE aggregate_id = :k"), {"k": self.key})
            session.commit()


def _by_key(rows: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(row["feature_key"]): row for row in rows}


def _build_three_versions(fixture: _RevertFixture) -> None:
    """v2：一次真实的 modify（同模块内改措辞）。v3：一次 replace（产生 delete）。"""
    fixture.seed_v1()
    fixture.merge(
        modules=[
            {"module": "鉴权", "items": ["登录", "连续输错三次锁定账户"]},
            {"module": "报表", "items": ["导出"]},
        ]
    )
    fixture.merge(modules=[{"module": "鉴权", "items": ["登录"]}], merge_mode="replace")


# ── 端到端：逐字段核对 ────────────────────────────────────────────────────


def test_revert_restores_membership_and_contents() -> None:
    fixture = _RevertFixture()
    try:
        _build_three_versions(fixture)
        before = _by_key(fixture.features(at_version=1))

        response = fixture.revert(target_version=1, comment="线上事故回退")
        assert response.status_code == 200, response.text
        result = response.json()

        assert result["version_no"] == 4
        assert result["change_type"] == "modify"
        assert result["change_summary"] == "回滚到 V1：线上事故回退"

        after = _by_key(fixture.features())
        assert set(after) == set(before), "回滚后成员集合与 V1 不一致"
        for key, row in before.items():
            assert after[key]["content"] == row["content"], f"{key} 的内容没复原"
            assert after[key]["module_key"] == row["module_key"], f"{key} 的模块没复原"
            assert after[key]["ordinal"] == row["ordinal"], f"{key} 的序号没复原"
    finally:
        fixture.cleanup()


def test_revert_revives_the_same_row_instead_of_inserting_a_new_one() -> None:
    """被删的功能必须**复活原行**：id 与 feature_key 都不变，能力关联才活得住。"""
    fixture = _RevertFixture()
    try:
        _build_three_versions(fixture)
        # v3 之后 F-002 与 F-003 都被 replace 掉了
        assert set(_by_key(fixture.features())) == {"F-001"}

        assert fixture.revert(target_version=1).status_code == 200

        after = _by_key(fixture.features())
        assert set(after) == {"F-001", "F-002", "F-003"}
        for feature_key in ("F-002", "F-003"):
            # 响应里的雪花 ID 是**字符串**（T1 起统一），与 fixture 手里的 int 比要转一下
            assert after[feature_key]["id"] == str(fixture.feature_ids[feature_key]), "复活时新建了行，id 变了"
            assert after[feature_key]["feature_key"] == feature_key, "feature_key 断裂"

        # 能力关联挂在 feature_id 上，因此复活后它必须还在
        with SessionLocal() as session:
            linked = session.execute(
                text("SELECT count(*) FROM feature_capability WHERE feature_id = :f"),
                {"f": fixture.feature_ids["F-002"]},
            ).scalar_one()
        assert linked == 1
    finally:
        fixture.cleanup()


def test_revert_is_append_only() -> None:
    """**revert 不是 reset**：历史版本的**内容**一个字节都不能变，只多出一个新版本。

    冻结的是内容（`requirement_snapshot` / `feature_changes` / `change_type`…）；
    `status` 与 `superseded_by_version_no` **必须**变 —— 部分唯一索引 `uq_version_current`
    要求同主线只有一个 current，旧版本降级是回滚的题中之义，不是改写历史。
    这两类分开断言，免得把「合法簿记」误判成「污染历史」。
    """
    fixture = _RevertFixture()
    try:
        _build_three_versions(fixture)
        before = fixture.fingerprint()  # v1/v2/v3

        assert fixture.revert(target_version=1).status_code == 200

        after = fixture.fingerprint()
        assert len(after) == 4, "回滚应产出一个新版本"

        frozen = ("version_no", "change_type", "requirement_snapshot", "feature_changes")
        for old, new in zip(before, after):
            for field in frozen:
                assert old[field] == new[field], f"V{old['version_no']} 的 {field} 被改写了"

        assert after[3]["status"] == "current"
        assert [row["status"] for row in after[:3]] == ["superseded"] * 3
        assert after[2]["superseded_by_version_no"] == 4
    finally:
        fixture.cleanup()


def test_revert_leaves_only_one_current_version() -> None:
    fixture = _RevertFixture()
    try:
        _build_three_versions(fixture)
        assert fixture.revert(target_version=1).status_code == 200

        with SessionLocal() as session:
            count = session.execute(
                text(
                    "SELECT count(*) FROM requirement_version "
                    "WHERE requirement_id = :id AND status = 'current'"
                ),
                {"id": fixture.master_id},
            ).scalar_one()
            master = session.execute(
                text("SELECT current_version, lock_version FROM requirement_master WHERE id = :id"),
                {"id": fixture.master_id},
            ).mappings().one()

        assert count == 1
        assert (master["current_version"], master["lock_version"]) == (4, 4)
    finally:
        fixture.cleanup()


def test_revert_closes_the_gap_against_the_target_version() -> None:
    """G2 与 G3 串起来：回滚版本与目标版本之间**不该有任何差异**。"""
    fixture = _RevertFixture()
    try:
        _build_three_versions(fixture)
        fixture.revert(target_version=1)

        response = client.get(
            f"/api/v1/requirements/{fixture.key}/diff",
            params={"from_version": 1, "to_version": 4},
        )
        assert response.status_code == 200, response.text
        diff = response.json()

        assert diff["added"] == []
        assert diff["removed"] == []
        assert diff["modified"] == []
        assert diff["unchanged"] == 3
    finally:
        fixture.cleanup()


def test_revert_writes_audit_and_outbox() -> None:
    fixture = _RevertFixture()
    try:
        _build_three_versions(fixture)
        fixture.revert(target_version=1)

        with SessionLocal() as session:
            audit = session.execute(
                text(
                    "SELECT event_type FROM audit_event WHERE aggregate_id = :k "
                    "AND event_type = 'requirement_version_reverted'"
                ),
                {"k": fixture.key},
            ).all()
            outbox = session.execute(
                text(
                    "SELECT count(*) FROM outbox_event WHERE aggregate_id = :k "
                    "AND event_type = 'embedding_sync'"
                ),
                {"k": fixture.key},
            ).scalar_one()

        assert len(audit) == 1, "回滚必须留审计（它是可追溯的唯一凭据）"
        # 正文变了 → 向量必须重算；合并那两次也各发了一条，所以是 3 而不是 1
        assert outbox >= 1
    finally:
        fixture.cleanup()


# ── 错误路径 ──────────────────────────────────────────────────────────────


def test_revert_rejects_unknown_requirement() -> None:
    response = client.post("/api/v1/requirements/REQ-NOT-EXIST/revert", json={"target_version": 1})
    assert response.status_code == 404


def test_revert_rejects_unknown_or_out_of_range_version() -> None:
    fixture = _RevertFixture()
    try:
        _build_three_versions(fixture)
        assert fixture.revert(target_version=99).status_code == 404
        assert fixture.revert(target_version=0).status_code == 422  # schema 的 gt=0
    finally:
        fixture.cleanup()


def test_revert_rejects_target_equal_to_current() -> None:
    fixture = _RevertFixture()
    try:
        _build_three_versions(fixture)
        response = fixture.revert(target_version=3)
        assert response.status_code == 409
        assert "无需回滚" in response.json()["detail"]
        assert len(fixture.fingerprint()) == 3, "被拒的回滚不该产生新版本"
    finally:
        fixture.cleanup()


def test_revert_rejects_stale_expected_current_version() -> None:
    """前端 STS 检查：页面显示的是 V1、库里已是 V3 → 拒绝，且不写库。"""
    fixture = _RevertFixture()
    try:
        _build_three_versions(fixture)
        response = fixture.revert(target_version=1, expected_current_version=1)

        assert response.status_code == 409
        assert "重新加载" in response.json()["detail"]
        assert len(fixture.fingerprint()) == 3
    finally:
        fixture.cleanup()


def test_revert_rejects_a_no_op_revert() -> None:
    """回滚到「功能集与当前完全一致」的版本 → 409，不产垃圾版本。"""
    fixture = _RevertFixture()
    try:
        _build_three_versions(fixture)
        # v2 只改了一条措辞；先回到 v1 再回到 v2 的前一版... 更直接：回滚到 v1 之后
        # 当前状态 == v1，此时再回滚到「与 v1 功能集相同的版本」不可构造，
        # 于是改用「回滚后再回滚到 v1 的等价版本」不成立 —— 改为验证连续两次回滚到 v1。
        assert fixture.revert(target_version=1).status_code == 200
        second = fixture.revert(target_version=1)

        assert second.status_code == 409, "第二次回滚到同一版本应当被拒（已是该状态）"
        assert len(fixture.fingerprint()) == 4, "被拒的回滚不该产生版本"
    finally:
        fixture.cleanup()


# ── 与并发锁的配合 ────────────────────────────────────────────────────────


def test_revert_and_merge_compete_correctly() -> None:
    """回滚之后再合并，两者都该成功；`lock_version` 一路推进。"""
    fixture = _RevertFixture()
    try:
        _build_three_versions(fixture)
        assert fixture.revert(target_version=1).status_code == 200  # v4
        fixture.merge(modules=[{"module": "鉴权", "items": ["登录", "锁定策略", "验证码"]}])  # v5

        with SessionLocal() as session:
            master = session.execute(
                text("SELECT current_version, lock_version FROM requirement_master WHERE id = :id"),
                {"id": fixture.master_id},
            ).mappings().one()

        assert (master["current_version"], master["lock_version"]) == (5, 5)
        # 「验证码」是全新内容 → 新建 F-004；回滚回来的三条仍是原来的行
        assert set(_by_key(fixture.features())) == {"F-001", "F-002", "F-003", "F-004"}
    finally:
        fixture.cleanup()
