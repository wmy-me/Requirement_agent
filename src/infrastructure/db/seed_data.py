"""Seed helper for inserting realistic initial requirement records."""

from __future__ import annotations

from src.domain.requirement import RequirementMaster, RequirementSource
from src.infrastructure.db.repositories import RequirementMasterRepository, RequirementSourceRepository
from src.infrastructure.embedding.embedding_service import EmbeddingService
from src.infrastructure.vector.pgvector_repository import RequirementVectorRepository


SEED_REQUIREMENTS: list[dict[str, str]] = [
    {
        "requirement_key": "REQ-100001",
        "requirement_name": "用户登录与验证码校验",
        "final_requirement": "支持手机号登录、短信验证码校验、失败重试和登录日志记录。",
    },
    {
        "requirement_key": "REQ-100002",
        "requirement_name": "角色与权限管理",
        "final_requirement": "支持用户角色分配、菜单权限控制、审批授权和权限变更审计。",
    },
    {
        "requirement_key": "REQ-100003",
        "requirement_name": "订单支付审批流程",
        "final_requirement": "订单支付需要支持多级审批、退款复核和审批记录追踪。",
    },
    {
        "requirement_key": "REQ-100004",
        "requirement_name": "报表查询与导出",
        "final_requirement": "支持报表筛选、导出、保存查询条件和导出记录审计。",
    },
    {
        "requirement_key": "REQ-100005",
        "requirement_name": "需求审核工作台",
        "final_requirement": "支持审核任务分配、approve/edit/reject 和审核意见留痕。",
    },
]


def seed_requirement_master() -> list[str]:
    master_repo = RequirementMasterRepository()
    vector_repo = RequirementVectorRepository()
    embedding_service = EmbeddingService()
    source_repo = RequirementSourceRepository()
    inserted: list[str] = []

    for index, item in enumerate(SEED_REQUIREMENTS, start=1):
        source = RequirementSource(
            idempotency_key=f"seed-requirement-{index:03d}",
            source_type="seed",
            requester_id="system",
            requester_name="system",
            original_text=item["final_requirement"],
            metadata={"seed": True, "requirement_key": item["requirement_key"]},
        )
        source_repo.save(source)
        requirement = RequirementMaster(
            requirement_key=item["requirement_key"],
            requirement_name=item["requirement_name"],
            final_requirement=item["final_requirement"],
            current_version=1,
            status="active",
            lock_version=0,
        )
        saved_requirement = master_repo.save(requirement)
        requirement_id = saved_requirement.id or index
        vector_repo.upsert(
            requirement_id,
            embedding_service.embed(item["final_requirement"]),
            source_text=item["final_requirement"],
        )
        inserted.append(saved_requirement.requirement_key)

    return inserted
