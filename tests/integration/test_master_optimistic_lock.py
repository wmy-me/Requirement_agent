"""`requirement_master` 的乐观锁 CAS（G 批 · G1）。

打真实库。这里要验的是**一条我只从 Postgres 文档读到、没在本项目里跑过的语义**：

    `INSERT ... ON CONFLICT DO UPDATE ... WHERE <条件>` 条件为假时 —— 不更新、不报错、
    且 `RETURNING` 不返回该行。

乐观锁的判据整个建立在它上面（「RETURNING 空 ⟺ 冲突」），所以必须有实测钉死，
不能只信推理。顺带钉死两条回归：**不传期望值时行为与从前一致**（热路径不能被改动波及），
以及**冲突时那一行真的没被写过**。
"""

from __future__ import annotations

import os
import uuid

os.environ.setdefault("API_AUTH_TOKEN", "test-api-token")

from sqlalchemy import text

from requirement_agent.domain.requirement import RequirementMaster
from requirement_agent.infrastructure.db.repositories import (
    ConcurrentModificationError,
    RequirementMasterRepository,
)
from requirement_agent.infrastructure.db.session import SessionLocal

master_repo = RequirementMasterRepository()


def _new_id() -> int:
    return uuid.uuid4().int % 9_000_000_000_000_000 + 1_000_000_000_000_000


class _MasterFixture:
    def __init__(self) -> None:
        self.tag = uuid.uuid4().hex[:8]
        self.master_id = _new_id()
        self.key = f"REQ-LK-{self.tag}"

    def cleanup(self) -> None:
        with SessionLocal() as session:
            session.execute(text("DELETE FROM requirement_master WHERE requirement_key = :k"), {"k": self.key})
            session.commit()

    def insert(self, *, lock_version: int = 1, current_version: int = 1) -> None:
        with SessionLocal() as session:
            session.execute(
                text(
                    "INSERT INTO requirement_master (id, requirement_key, requirement_name, "
                    "final_requirement, current_version, status, lock_version) "
                    "VALUES (:id, :k, :n, '初始正文', :cv, 'active', :lv)"
                ),
                {
                    "id": self.master_id,
                    "k": self.key,
                    "n": f"锁测试-{self.tag}",
                    "cv": current_version,
                    "lv": lock_version,
                },
            )
            session.commit()

    def snapshot(self) -> dict[str, object]:
        with SessionLocal() as session:
            row = session.execute(
                text(
                    "SELECT requirement_name, final_requirement, current_version, lock_version "
                    "FROM requirement_master WHERE requirement_key = :k"
                ),
                {"k": self.key},
            ).mappings().one()
            return dict(row)


def _master(key: str, *, current_version: int, lock_version: int, name: str, body: str) -> RequirementMaster:
    return RequirementMaster(
        requirement_key=key,
        requirement_name=name,
        final_requirement=body,
        current_version=current_version,
        status="active",
        lock_version=lock_version,
    )


# ── 无冲突路径（回归：不传期望值时行为不变） ──────────────────────────────


def test_insert_without_expected_lock_version_still_works() -> None:
    """不带 CAS 的写入必须照旧 —— 新建主需求走的就是这条。"""
    fixture = _MasterFixture()
    try:
        master_repo.save(_master(fixture.key, current_version=0, lock_version=0, name="新建", body="正文"))
        assert fixture.snapshot()["current_version"] == 0
    finally:
        fixture.cleanup()


def test_update_without_expected_lock_version_ignores_staleness() -> None:
    """不传期望值时**不做**校验（既有调用方的行为不能被本次改动改变）。"""
    fixture = _MasterFixture()
    try:
        fixture.insert(lock_version=5, current_version=5)
        master_repo.save(_master(fixture.key, current_version=6, lock_version=6, name="照写", body="新正文"))

        assert fixture.snapshot()["lock_version"] == 6
    finally:
        fixture.cleanup()


# ── CAS 的三条路径 ────────────────────────────────────────────────────────


def test_matching_expected_lock_version_updates() -> None:
    fixture = _MasterFixture()
    try:
        fixture.insert(lock_version=3, current_version=3)
        master_repo.save(
            _master(fixture.key, current_version=4, lock_version=4, name="更新", body="新正文"),
            expected_lock_version=3,
        )

        snapshot = fixture.snapshot()
        assert (snapshot["current_version"], snapshot["lock_version"]) == (4, 4)
        assert snapshot["final_requirement"] == "新正文"
    finally:
        fixture.cleanup()


def test_stale_expected_lock_version_raises_and_leaves_row_untouched() -> None:
    """**核心断言**：CAS 失败时抛具名异常，且那一行**逐字段未被写过**。"""
    fixture = _MasterFixture()
    try:
        fixture.insert(lock_version=3, current_version=3)
        before = fixture.snapshot()

        try:
            master_repo.save(
                _master(fixture.key, current_version=99, lock_version=99, name="不该落库", body="不该落库"),
                expected_lock_version=2,  # 陈旧（真实值是 3）
            )
        except ConcurrentModificationError as exc:
            assert exc.expected_lock_version == 2
            assert exc.requirement_key == fixture.key
        else:  # pragma: no cover - 失败时给出可读原因
            raise AssertionError("期望抛 ConcurrentModificationError，实际静默写入了")

        assert fixture.snapshot() == before, "CAS 失败却改了行 —— 「RETURNING 空即冲突」的判据不成立"
    finally:
        fixture.cleanup()


def test_stale_write_after_a_real_commit_is_rejected() -> None:
    """模拟真实竞态：另一个事务先提交了，本事务拿着开头的旧值写回 → 必须被拒。

    对应「两个审核人同时合并进同一个 REQ」：后者的整份工作应被丢弃（409），
    而不是静默覆盖前者。
    """
    fixture = _MasterFixture()
    try:
        fixture.insert(lock_version=1, current_version=1)

        # 事务 A：读到 lock_version=1
        with SessionLocal() as session_a:
            read_a = master_repo.get_by_key(fixture.key)
            assert read_a is not None and read_a.lock_version == 1

            # 事务 B：先完成一次合并（current_version 1 → 2）
            master_repo.save(
                _master(fixture.key, current_version=2, lock_version=2, name="B 的合并", body="B 的正文"),
                expected_lock_version=1,
            )

            # 事务 A 拿着陈旧的 1 写回 → 撞锁
            read_a.current_version = 2
            read_a.lock_version = 2
            read_a.final_requirement = "A 的正文"
            try:
                master_repo.save(read_a, session=session_a, expected_lock_version=1)
            except ConcurrentModificationError:
                session_a.rollback()
            else:  # pragma: no cover
                raise AssertionError("陈旧的合并没有被拒绝，最后一个写者会静默覆盖前一个")

        snapshot = fixture.snapshot()
        assert snapshot["final_requirement"] == "B 的正文", "冲突方的写入泄漏了"
        assert snapshot["current_version"] == 2
    finally:
        fixture.cleanup()


def test_expected_none_does_not_append_a_where_clause() -> None:
    """显式传 None 与不传等价（`save` 的默认值路径不能被拼坏的 SQL 波及）。"""
    fixture = _MasterFixture()
    try:
        fixture.insert(lock_version=4, current_version=4)
        master_repo.save(
            _master(fixture.key, current_version=5, lock_version=5, name="显式 None", body="正文"),
            expected_lock_version=None,
        )

        assert fixture.snapshot()["lock_version"] == 5
    finally:
        fixture.cleanup()
