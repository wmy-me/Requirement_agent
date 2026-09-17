"""真实审核提交的端到端路径（G 批 · G1 的热路径守卫）。

**为什么单独要有这一个文件**：在 G 批之前，全仓**没有任何测试**跑过真实库上的
`ReviewService.submit_decision`（`grep submit_decision tests/` 只命中带假仓储的单测）。
而 G1 把乐观锁 CAS 加在了这条路上 —— 一个「期望值取值有偏差」的实现会让**每一次审核
都返回 409**，而假仓储的单测**全绿**（它不校验锁）。所以必须有真实库的端到端：

1. 新建路径（无目标 REQ）能正常产出 v1；
2. **合并路径（带目标 REQ）不被 CAS 误杀** —— 这是本轮改动的头号回归风险；
3. 合并真的产出新版本、旧版本被 supersede、`current` 仍只有一条。

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

# B1 起 API 需要鉴权：`API_AUTH_TOKEN` 是兼容入口，等同于一个 admin token。
# 测具体的 401/403 行为请另建不带头的 TestClient（见 tests/integration/test_api_auth.py）。
client = TestClient(app, headers={"Authorization": "Bearer test-api-token"})


def _new_id() -> int:
    return uuid.uuid4().int % 9_000_000_000_000_000 + 1_000_000_000_000_000


def _sha(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class _CommitFixture:
    """一条目标 REQ（2 条功能）+ 一条待审来源（来源里含 1 条改写、1 条新增）。"""

    def __init__(self) -> None:
        self.tag = uuid.uuid4().hex[:10]
        self.master_id = _new_id()
        self.key = f"REQ-COMMIT-{self.tag}"
        self.source_id = _new_id()
        # 只删「当前那个」会漏掉前面造的 —— 连续合并的用例会造多条来源。
        self.source_ids: list[int] = [self.source_id]

    def seed_target(self) -> None:
        with SessionLocal() as session:
            session.execute(
                text(
                    "INSERT INTO requirement_master (id, requirement_key, requirement_name, "
                    "final_requirement, current_version, status, lock_version) "
                    "VALUES (:id, :k, :n, 'x', 1, 'active', 1)"
                ),
                {"id": self.master_id, "k": self.key, "n": f"提交路径-{self.tag}"},
            )
            for ordinal, content in enumerate(["登录", "导出"], start=1):
                session.execute(
                    text(
                        "INSERT INTO requirement_feature (id, requirement_id, feature_key, content, "
                        "status, ordinal, origin_source_id, origin_requirement_key, origin_version_no, "
                        "provenance, content_hash) "
                        "VALUES (:id, :rid, :fk, :c, 'active', :o, NULL, :rk, 1, CAST('[]' AS JSONB), :h)"
                    ),
                    {
                        "id": _new_id(),
                        "rid": self.master_id,
                        "fk": f"F-{ordinal:03d}",
                        "c": content,
                        "o": ordinal,
                        "rk": self.key,
                        "h": _sha(content),
                    },
                )
            # current_version=1 就该有一条 v1 版本行 —— 少了它，合并路径的
            # `supersede_current` 就没东西可降级，而「刚好没人考到」正是它出错的温床。
            session.execute(
                text(
                    "INSERT INTO requirement_version (id, requirement_id, version_no, version_title, "
                    "change_type, requirement_snapshot, change_summary, created_by, reviewed_by, status, "
                    "capability_snapshot, constraint_snapshot) "
                    "VALUES (:id, :rid, 1, 't', 'new', '登录\n导出', '初始版本', 'a', 'a', 'current', "
                    "CAST('[]' AS JSONB), CAST('[]' AS JSONB))"
                ),
                {"id": _new_id(), "rid": self.master_id},
            )
            session.commit()

    def seed_source(self) -> None:
        """造一条待审来源。`_feature_candidates` 只读 `metadata.extracted`，不调模型。"""
        self.source_ids.append(self.source_id)
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
                    "id": self.source_id,
                    "ik": f"commit-{self.tag}",
                    "rq": f"commit-{self.tag}",
                    "txt": "登录、导出，外加一条全新的看板",
                    "meta": '{"extracted": {"requirement_title": "提交路径走查", '
                    '"requirements": ["登录", "导出", "看板"]}}',
                },
            )
            session.commit()

    def submit(self, *, target: str | None) -> dict[str, object]:
        # 注意：请求体里没有 `reviewer_id`（`ReviewSubmitRequest` 是 extra="forbid"），
        # reviewer 身份由服务端从 `settings.api_actor_id` 取；这里只能给 `reviewer_name`。
        payload: dict[str, object] = {
            "source_id": str(self.source_id),
            "decision": "approved",
            "reviewer_name": "提交路径走查",
        }
        if target is not None:
            payload["target_requirement_key"] = target
        response = client.post("/api/v1/reviews/submit", json=payload)
        assert response.status_code == 200, response.text
        return response.json()

    def versions(self) -> list[dict[str, object]]:
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    "SELECT version_no, status, change_type FROM requirement_version "
                    "WHERE requirement_id = :id ORDER BY version_no"
                ),
                {"id": self.master_id},
            ).mappings().all()
            return [dict(row) for row in rows]

    def cleanup(self, *, requirement_key: str | None = None) -> None:
        key = requirement_key or self.key
        with SessionLocal() as session:
            # 先按 key 找到本测试造出来的所有主线（新建路径的主线不在 fixture 手上）。
            ids = [
                int(row[0])
                for row in session.execute(
                    text("SELECT id FROM requirement_master WHERE requirement_key = :k"), {"k": key}
                ).all()
            ]
            for requirement_id in ids:
                session.execute(
                    text(
                        "DELETE FROM requirement_version_source WHERE version_id IN "
                        "(SELECT id FROM requirement_version WHERE requirement_id = :id)"
                    ),
                    {"id": requirement_id},
                )
                session.execute(
                    text("DELETE FROM requirement_version WHERE requirement_id = :id"),
                    {"id": requirement_id},
                )
                session.execute(
                    text("DELETE FROM requirement_feature WHERE requirement_id = :id"),
                    {"id": requirement_id},
                )
                session.execute(
                    text("DELETE FROM requirement_relation WHERE subject_requirement_id = :id OR "
                    "target_requirement_id = :id"),
                    {"id": requirement_id},
                )
                session.execute(
                    text("DELETE FROM requirement_master WHERE id = :id"), {"id": requirement_id}
                )
            for source_id in set(self.source_ids):
                session.execute(text("DELETE FROM requirement_review WHERE source_id = :s"), {"s": source_id})
                session.execute(text("DELETE FROM requirement_source WHERE id = :s"), {"s": source_id})
            session.execute(
                text("DELETE FROM outbox_event WHERE aggregate_id = :k"), {"k": key}
            )
            session.execute(text("DELETE FROM audit_event WHERE aggregate_id = :k"), {"k": key})
            session.commit()


# ── 新建路径 ──────────────────────────────────────────────────────────────


def test_new_master_path_commits_without_lock_conflict() -> None:
    """无目标 REQ → 新建。**这条路上不该出现任何锁冲突**（key 是新分配的）。"""
    fixture = _CommitFixture()
    created_key: str | None = None
    try:
        fixture.seed_source()
        result = fixture.submit(target=None)
        created_key = str(result["requirement_key"])

        assert result["version_no"] == 1
        assert created_key.startswith("REQ-")
        with SessionLocal() as session:
            row = session.execute(
                text(
                    "SELECT current_version, lock_version, final_requirement FROM requirement_master "
                    "WHERE requirement_key = :k"
                ),
                {"k": created_key},
            ).mappings().one()
        assert row["current_version"] == 1
        assert row["lock_version"] == 1
    finally:
        fixture.cleanup(requirement_key=created_key)


# ── 合并路径（CAS 的头号回归风险） ────────────────────────────────────────


def test_merge_path_is_not_killed_by_the_cas() -> None:
    """**G1 的头号回归**：带目标 REQ 的合并必须照常成功，而不是被乐观锁误判成冲突。

    期望值取自本次事务开头读到的 `lock_version`，写回时该值仍是 1（没人插队），
    所以 CAS 必然成立 —— 但如果抓取位置写错（取到 `next_version`），这里就会 409。
    """
    fixture = _CommitFixture()
    try:
        fixture.seed_target()
        fixture.seed_source()

        result = fixture.submit(target=fixture.key)

        assert result["version_no"] == 2
        assert result["requirement_key"] == fixture.key

        versions = fixture.versions()
        assert [(v["version_no"], v["status"]) for v in versions] == [(1, "superseded"), (2, "current")]
    finally:
        fixture.cleanup()


def test_merge_bumps_lock_version_and_keeps_a_single_current() -> None:
    """合并后 `lock_version` 必须跟着推进 —— 否则下一次 CAS 会永远成立、锁形同虚设。"""
    fixture = _CommitFixture()
    try:
        fixture.seed_target()
        fixture.seed_source()
        fixture.submit(target=fixture.key)

        with SessionLocal() as session:
            row = session.execute(
                text(
                    "SELECT current_version, lock_version FROM requirement_master WHERE id = :id"
                ),
                {"id": fixture.master_id},
            ).mappings().one()
            current_count = session.execute(
                text(
                    "SELECT count(*) FROM requirement_version "
                    "WHERE requirement_id = :id AND status = 'current'"
                ),
                {"id": fixture.master_id},
            ).scalar_one()

        assert (row["current_version"], row["lock_version"]) == (2, 2)
        assert current_count == 1
    finally:
        fixture.cleanup()


def test_second_merge_on_the_same_target_also_succeeds() -> None:
    """连续两次合并进同一个 REQ：第二次读到的 lock_version 已是 2，CAS 用 2 校验、照样成立。

    这条钉的是「锁不能把自己人锁住」—— 每次合并都会推进 lock_version，
    下一个审核人重新读页面就能拿到新值。
    """
    fixture = _CommitFixture()
    try:
        fixture.seed_target()
        fixture.seed_source()
        assert fixture.submit(target=fixture.key)["version_no"] == 2

        fixture.source_id = _new_id()
        fixture.tag = uuid.uuid4().hex[:10]
        fixture.seed_source()
        assert fixture.submit(target=fixture.key)["version_no"] == 3

        assert [(v["version_no"], v["status"]) for v in fixture.versions()] == [
            (1, "superseded"),
            (2, "superseded"),
            (3, "current"),
        ]
    finally:
        fixture.cleanup()
