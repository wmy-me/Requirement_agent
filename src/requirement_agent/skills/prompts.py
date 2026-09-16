"""统一的中文 Prompt 模板与结构化输出规范。

**唯一事实源**：`ExtractSkill` / `AnalyzeSkill` / `RiskSkill` 的 system / user prompt
一律取自本模块，不再各自内联，避免「同一 prompt 多处副本」的漂移。

约定：
- SYSTEM prompt 为固定字符串常量。
- USER prompt 由 builder 函数构造（用 f-string 拼接，而非 `str.format`）——
  避免 `raw_text` / `history` 等入参里含 `{}` 时 `.format()` 报错。
- 修改任何 prompt 时同步递增 `PROMPT_VERSION`，便于追溯。
"""

from __future__ import annotations

from typing import Any

# Prompt 版本标记：任何 prompt 变更都递增，便于追溯与后续灰度。
PROMPT_VERSION = "2026.09.15"


# —— 系统提示词（三个 Skill 共用，原先就与内联版本一致）——
EXTRACT_SYSTEM_PROMPT = (
    "你是一名资深业务需求分析师。请从原始需求文本中抽取结构化业务需求，"
    "返回严格的 JSON 对象，且仅返回 JSON，不要包含 markdown 代码块。"
)

ANALYZE_SYSTEM_PROMPT = (
    "你是一名企业需求分析师。请比较当前需求与历史需求的关系，判断其是否重复、相关、冲突或独立，"
    "并返回严格的 JSON 对象。"
)

RISK_SYSTEM_PROMPT = (
    "你是一名企业架构与风险评估专家。请基于业务质量、变更影响和技术复杂度，"
    "评估该需求的风险，并返回严格的 JSON 对象。"
)


# —— 用户提示词构造 ——
def build_extract_user_prompt(
    *,
    source_type: str,
    requester_name: str | None,
    raw_text: str,
) -> str:
    """构造抽取任务的 user prompt。"""
    return (
        "请从下面的需求来源文本中抽取结构化需求。\n"
        "请输出 JSON，字段说明如下：\n"
        "- requirement_title：简洁且明确的需求标题\n"
        "- summary：需求的业务摘要\n"
        "- requester_name：需求发起人或发起团队，若未知则写 null\n"
        "- source_type：需求来源渠道\n"
        "- business_domain：业务领域，例如 auth、order、report、data、workflow、general\n"
        "- priority：low、medium、high\n"
        "- tags：业务标签列表\n"
        "- requirements：子需求列表\n"
        "- modules：子需求的**模块分组**，形如 [{\"module\": \"登录\", \"items\": [\"…\", \"…\"]}]。"
        "  **能分组就必须给**（子需求天然按功能域可分时，如登录/报表/权限）；"
        "  **分不出就不给**（此时省略 modules，仅给扁平 requirements）\n"
        "- business_object：这条需求作用的**核心业务对象**，如「员工数据」「订单」「巡检计划」\n"
        "- capabilities：**每条子需求**对应的能力候选，形如\n"
        '  [{"raw_text": "支持按部门筛选并导出 Excel", "action": "导出", "object": "Excel",\n'
        '    "constraints": ["按部门筛选"]}]\n'
        "  action = 动词（导出/统计/创建/删除/推送/审批…），object = 宾语。"
        "**一条子需求一个元素。**\n"
        "  constraints = 对**范围/维度/批量**的限定，即「按什么筛」或「批量」，例如\n"
        "    「按部门筛选」「按门店」「按时间维度」「按周期」「批量」。\n"
        "  ⚠️ 下列**都不是** constraints，必须排除：\n"
        "    · 状态或等级的**枚举值**（待执行、执行中、已完成、一般、严重、致命）\n"
        "    · 角色或对象（巡检员、整改责任人、管理员）\n"
        "    · 动作本身（拍照上传、填写描述、推送提醒、跟踪闭环）\n"
        "    · 字段名或展示位置（首页看板、本月关键指标）\n"
        "  没有限定就给 []，**宁可空着也不要凑数**。\n"
        "- raw_text：原始文本\n"
        f"source_type={source_type}\nrequester_name={requester_name or 'unknown'}\nraw_text:\n{raw_text}"
    )


def build_analyze_user_prompt(*, extracted_json: Any, history: Any) -> str:
    """构造冲突/重复分析任务的 user prompt。

    `extracted_json` 为 `ExtractedRequirement.model_dump(mode="json")` 的结果，
    `history` 为历史需求候选列表。
    """
    return (
        "请分析当前需求与历史需求的关系，并判断是否存在重复、关联、冲突或独立情况。\n"
        "返回 JSON，字段包括：duplicate、related、conflict、independent、reasoning、candidates。\n"
        "candidates 中每项必须有 requirement_key、title、similarity、reason。\n"
        f"当前需求：{extracted_json}\n"
        f"历史需求：{history}"
    )


def build_risk_user_prompt(*, requirement_json: Any) -> str:
    """构造风险评估任务的 user prompt。"""
    return (
        "请评估当前需求的风险，字段包括：quality_risk、change_risk、technical_impact_risk、confidence。\n"
        "quality_risk、change_risk、technical_impact_risk 的合法值为 low、medium、high。\n"
        f"需求内容：{requirement_json}"
    )


__all__ = [
    "PROMPT_VERSION",
    "EXTRACT_SYSTEM_PROMPT",
    "ANALYZE_SYSTEM_PROMPT",
    "RISK_SYSTEM_PROMPT",
    "build_extract_user_prompt",
    "build_analyze_user_prompt",
    "build_risk_user_prompt",
]
