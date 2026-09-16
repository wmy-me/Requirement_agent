"""回滚的应用服务：把一条需求主线退回某个历史版本的状态（事务外壳）。

**为什么单开一条写入路径，而不是复用 `commit_requirement_node`。** 那个节点是
**来源驱动**的：它从 `ctx["source_id"]` 取来源、靠 `feature_rows_for_source` 产出功能行，
结束前还要 `link_source`、确认关系边、把来源置成 `committed`、发 embedding outbox。
回滚**没有来源**（它是人对库的操作），硬套只有两条路：造一个假来源往
`requirement_source` 里写脏数据，或给节点加一堆 `if is_revert` 分支 —— 都比另开一条
六十行的路径差。

**回滚的语义是 append-only 的 revert，不是 reset。** 历史版本一行都不改，
新版本照常排到队尾，`change_type` 记 `modify`（`requirement_version` 的 CHECK 只允许
`new/add/modify/delete` 四值，没有 `revert`），真正的「这是回滚」写在
`diff_payload.kind = "revert"` 里 —— 机器可读，且不需要 schema 变更。
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from requirement_agent.domain.feature_history import FeatureState, PlannedRevertRow, plan_revert
from requirement_agent.domain.requirement import AuditEvent, RequirementVersion
from requirement_agent.infrastructure.db.repositories import (
    AuditRepository,
    RequirementFeatureRepository,
    RequirementMasterRepository,
    RequirementVersionRepository,
)
from requirement_agent.infrastructure.db.session import SessionLocal
from requirement_agent.infrastructure.worker.outbox import OutboxRepository

# 回滚相对上一版可能什么都不改（比如「回滚到 V2」而当前内容恰好与 V2 相同）。
# 这时不该产出垃圾版本 —— 由调用方翻成 409。
NO_CHANGE_MESSAGE = "回滚目标与当前功能集完全一致，未产生任何变更"


class RevertService:
    """回滚的事务持有者：开 session → 复原目标状态 → 写新版本 → commit/rollback/close。"""

    def __init__(
        self,
        master_repo: RequirementMasterRepository | None = None,
        feature_repo: RequirementFeatureRepository | None = None,
        version_repo: RequirementVersionRepository | None = None,
        audit_repo: AuditRepository | None = None,
        outbox_repo: OutboxRepository | None = None,
        session_factory: Callable[[], Session] = SessionLocal,
    ) -> None:
        self.master_repo = master_repo or RequirementMasterRepository()
        self.feature_repo = feature_repo or RequirementFeatureRepository()
        self.version_repo = version_repo or RequirementVersionRepository()
        self.audit_repo = audit_repo or AuditRepository()
        self.outbox_repo = outbox_repo or OutboxRepository()
        self.session_factory = session_factory

    def revert_to_version(
        self,
        *,
        requirement_key: str,
        target_version: int,
        actor_id: str,
        comment: str | None = None,
        expected_current_version: int | None = None,
    ) -> dict[str, object]:
        """把 `requirement_key` 回滚到 `target_version` 时刻的功能集。

        异常语义（由路由翻译成 HTTP）：不存在的 key / 版本 → `LookupError`；
        「没有变化」「目标就是当前版本」「前端页面陈旧」→ `ValueError`；
        并发被别人改过 → `ConcurrentModificationError`。

        `expected_current_version` 是**可选的前端 STS 检查**（页面加载时看到的版本号）：
        `lock_version` 只防得住「同一事务窗口内」的并发，防不住「人盯着五分钟前的页面点回滚」。
        """
        session = self.session_factory()
        try:
            master = self.master_repo.get_by_key(requirement_key, session=session)
            if master is None or master.id is None:
                raise LookupError(f"requirement_key={requirement_key} not found")

            # 乐观锁期望值：必须是**改动之前**读到的值。
            expected_lock_version = int(master.lock_version or 0)
            current_version = int(master.current_version or 0)
            requirement_id = int(master.id)

            if expected_current_version is not None and int(expected_current_version) != current_version:
                raise ValueError(
                    f"该需求已是 V{current_version}，页面显示的是 V{expected_current_version}，请重新加载"
                )
            if target_version < 1:
                raise LookupError(f"version V{target_version} not found for {requirement_key}")
            if target_version == current_version:
                raise ValueError(f"该需求已经是 V{target_version}，无需回滚")
            if target_version > current_version:
                raise LookupError(f"version V{target_version} not found for {requirement_key}")

            target = self.version_repo.get_by_version_no(
                requirement_id=requirement_id, version_no=target_version, session=session
            )
            if target is None:
                raise LookupError(f"version V{target_version} not found for {requirement_key}")

            # 目标时刻的功能集：复用 G2 的复原（与 `features?at_version=N` 是同一实现，
            # 不另写一份 —— 两份实现迟早分叉）。
            target_states = self.feature_repo.list_by_requirement_key(
                requirement_key, at_version=target_version, session=session
            )
            current_rows = self.feature_repo.list_by_requirement_key(
                requirement_key, include_deleted=True, session=session
            )
            plan = plan_revert(current_rows, [_state_from_row(row) for row in target_states])
            if not any(item.op in {"add", "modify", "delete"} for item in plan):
                raise ValueError(NO_CHANGE_MESSAGE)

            next_version = current_version + 1
            changes = self.feature_repo.reconcile_features(
                requirement_id,
                plan,
                requirement_key=requirement_key,
                version_no=next_version,
                session=session,
            )
            snapshot = self.feature_repo.join_active_features(requirement_id, session=session)
            if not snapshot:
                # 极端情形（回滚到一条功能都没有的版本）：保留目标版本的正文，
                # 避免把 final_requirement 写成空串。
                snapshot = str(target["requirement_snapshot"] or "")

            summary = f"回滚到 V{target_version}"
            if comment and comment.strip():
                summary = f"{summary}：{comment.strip()}"

            current_row = self.version_repo.get_current(master_id=requirement_id, session=session)
            version = RequirementVersion(
                requirement_id=requirement_id,
                parent_version_id=(current_row or {}).get("id"),
                parent_version_no=current_version,
                version_no=next_version,
                version_title=str(target["version_title"] or master.requirement_name)[:80],
                change_type="modify",
                requirement_snapshot=snapshot,
                change_summary=summary,
                feature_changes=changes,
                # 能力/条件快照跟着目标版本走：回滚后 `/capabilities` 读的是 current 版本的
                # 约束快照，只有复制目标版本的那份才与「回到 V{n}」自洽。
                capability_snapshot=list(target.get("capability_snapshot") or []),
                constraint_snapshot=list(target.get("constraint_snapshot") or []),
                diff_payload={
                    "kind": "revert",
                    "revert_from_version": current_version,
                    "revert_to_version": target_version,
                    "actor_id": actor_id,
                },
                created_by=actor_id,
                reviewed_by=actor_id,
            )

            # ⚠️ 顺序不能反：部分唯一索引 `uq_version_current` 不允许两个 current，
            # 而 save 插入时状态默认就是 current。先降级旧的，再插入新的。
            self.version_repo.supersede_current(
                requirement_id=requirement_id, keep_version_no=next_version, session=session
            )
            saved = self.version_repo.save(version, session=session)

            master.final_requirement = snapshot
            master.current_version = next_version
            master.status = "active"
            master.lock_version = next_version
            self.master_repo.save(master, session=session, expected_lock_version=expected_lock_version)

            self.audit_repo.record(
                AuditEvent(
                    trace_id=f"revert-{requirement_key}-{actor_id}",
                    event_type="requirement_version_reverted",
                    aggregate_type="requirement_master",
                    aggregate_id=requirement_key,
                    actor_type="reviewer",
                    actor_id=actor_id,
                    before_data={"current_version": current_version},
                    after_data={"current_version": next_version, "reverted_to": target_version},
                ),
                session=session,
            )
            # 正文变了 → 向量必须重算（与 commit 节点同一理由）。
            self.outbox_repo.enqueue(
                aggregate_type="requirement_master",
                aggregate_id=requirement_key,
                event_type="embedding_sync",
                payload={
                    "requirement_id": requirement_id,
                    "requirement_key": requirement_key,
                    "version_id": saved.id,
                    "version_no": saved.version_no,
                    "content": snapshot,
                },
                session=session,
            )

            session.commit()
            counts = _summarize(plan)
            return {
                "requirement_key": requirement_key,
                "from_version": current_version,
                "target_version": target_version,
                "version_no": next_version,
                "change_type": "modify",
                "change_summary": summary,
                "requirement_snapshot": snapshot,
                "feature_changes": changes,
                "summary": counts,
                "lock_version": next_version,
            }
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def _state_from_row(row: dict[str, object]) -> FeatureState:
    """把复原出来的 feature 行（repo 形状）转回领域值对象。"""
    return FeatureState(
        feature_key=str(row["feature_key"]),
        content=str(row["content"]),
        ordinal=int(row.get("ordinal") or 0),
        module_key=row.get("module_key"),  # type: ignore[arg-type]
        module_name=row.get("module_name"),  # type: ignore[arg-type]
    )


def _summarize(plan: list[PlannedRevertRow]) -> dict[str, int]:
    """按 op 计数，另给回滚后生效的功能数（与合并预览的 summary 同口径）。

    `active_after` 直接由计划的 op 推：`add` / `modify` / `keep` 三类都是回滚后仍在的行，
    `delete` 不在。
    """
    counts = {"add": 0, "modify": 0, "delete": 0, "keep": 0}
    for item in plan:
        if item.op in counts:
            counts[item.op] += 1
    return {
        "add": counts["add"],
        "modify": counts["modify"],
        "delete": counts["delete"],
        "keep": counts["keep"],
        "active_after": counts["add"] + counts["modify"] + counts["keep"],
    }
