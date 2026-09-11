"""统一的中文 Prompt 模板与结构化输出规范。"""

from __future__ import annotations

EXTRACT_SYSTEM_PROMPT = (
    "你是一名资深业务需求分析师。请从原始需求文本中抽取结构化业务需求，"
    "返回严格的 JSON 对象，且仅返回 JSON，不要包含 markdown 代码块。"
)

EXTRACT_USER_PROMPT_TEMPLATE = """
请从下面的需求来源文本中抽取结构化需求。
要求：
1. 返回有效 JSON，不要包含说明文字和 markdown。
2. 字段必须包含：requirement_title、summary、requester_name、source_type、business_domain、priority、tags、requirements、raw_text。
3. requirement_title 是简洁且明确的标题。
4. summary 是需求的业务摘要。
5. tags 是业务标签列表，值建议是中文关键词。
6. requirements 是子需求列表。
7. 若 requester_name 未知，则使用 null。
8. business_domain 仅可取 auth、order、report、data、workflow、general。
9. priority 仅可取 low、medium、high。

source_type={source_type}
requester_name={requester_name}
raw_text:
{raw_text}
""".strip()

ANALYZE_SYSTEM_PROMPT = (
    "你是一名企业需求分析师。请比较当前需求与历史需求的关系，判断其是否重复、相关、冲突或独立，"
    "并返回严格的 JSON 对象。"
)

ANALYZE_USER_PROMPT_TEMPLATE = """
请分析当前需求与历史需求的关系，并判断是否存在重复、关联、冲突或独立情况。
返回 JSON，字段包括：duplicate、related、conflict、independent、reasoning、candidates。
要求：
1. duplicate、related、conflict、independent 必须是布尔值。
2. reasoning 必须是中文结论说明。
3. candidates 是相似候选列表。
4. candidates 中每项必须有 requirement_key、title、similarity、reason。
5. similarity 应该是 0 到 1 之间的小数。

当前需求：{extracted_json}
历史需求：{history_json}
""".strip()

RISK_SYSTEM_PROMPT = (
    "你是一名企业架构与风险评估专家。请基于业务质量、变更影响和技术复杂度，"
    "评估该需求的风险，并返回严格的 JSON 对象。"
)

RISK_USER_PROMPT_TEMPLATE = """
请评估当前需求的风险。
返回 JSON，字段包括：quality_risk、change_risk、technical_impact_risk、confidence。
要求：
1. quality_risk、change_risk、technical_impact_risk 只允许为 low、medium、high。
2. confidence 是 0 到 1 之间的小数。
3. 只返回 JSON，不要解释。

需求内容：{requirement_json}
""".strip()
