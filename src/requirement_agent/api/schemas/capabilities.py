"""能力相关的 API Schema（方案批次 5 —— 人工裁决）。

两个裁决是**不同的问题**，别混：

- `FeatureCapabilityReviewRequest`：**这条需求有没有这个能力** ——
  改 `feature_capability.review_status`
- `CapabilityStatusRequest`：**这条能力本身成不成立** ——
  改 `capability.status`（`pending_confirmation` → `active`）

按职责边界（方案 §11），两者都**只能由人工触发**，没有自动通道。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FeatureCapabilityReviewRequest(BaseModel):
    """裁决「某条功能 ↔ 某个能力」的关联。"""

    model_config = ConfigDict(extra="forbid")

    feature_id: int = Field(gt=0)
    capability_id: int = Field(gt=0)
    # 只允许 confirmed / dismissed —— 「撤回裁决」回到 proposed 需要重新分析，
    # 与关系裁决端点（RequirementRelationUpdateRequest）保持同一约定
    status: Literal["confirmed", "dismissed"]


class CapabilityStatusRequest(BaseModel):
    """裁决「某条能力」本身是否正式成立。"""

    model_config = ConfigDict(extra="forbid")

    # active：确认成立，此后才参与精确匹配
    # deprecated：停用（历史引用保留，不再匹配新需求）
    # pending_confirmation：打回提案态（等价于撤回确认）
    status: Literal["active", "deprecated", "pending_confirmation"]
    display_name: str | None = Field(default=None, max_length=200)


class ConstraintAliasRequest(BaseModel):
    """把一个原始表达登记为某条件的别名（人工四个选项之一：作为别名）。"""

    model_config = ConfigDict(extra="forbid")

    alias: str = Field(min_length=1, max_length=200)
    constraint_id: int = Field(gt=0)
