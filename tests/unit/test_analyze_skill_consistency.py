"""校验 AnalyzeSkill 对 LLM 布尔字段的交叉修正：消除 duplicate∧independent 等自相矛盾。

**B4 起这个文件的重心变了。** 以前它守的是「模型自报的 similarity 要跟本地阈值一致」，
而那个 similarity 是幻觉 —— 提示词要求模型返回一个它算不出来的数，模型只能把检索分数
原样回显（`0.7313209960078035` 逐位相同）。现在：

- 提示词**不再索要** similarity；
- `CandidateMatch.similarity` 由后端从真实余弦填；
- 模型点名的候选若在检索结果里查不到（编的 key），一律 `unverifiable`，**无法伪造相似度**。

于是「模型说什么」与「证据是什么」第一次真正分开：模型负责**选哪几条、为什么**，
后端负责**它们到底像不像**。下面两个方向都要钉住。
"""

from requirement_agent.agents.extract_agent import ExtractedRequirement
from requirement_agent.skills.analyze_skill import AnalyzeSkill


class FakeProvider:
    configured = True

    def __init__(self, text: str) -> None:
        self.text = text

    def is_configured(self) -> bool:
        return True

    def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        return self.text


def _extracted() -> ExtractedRequirement:
    return ExtractedRequirement(
        requirement_title="登录增强",
        summary="支持短信验证码登录",
        business_domain="auth",
        tags=["登录"],
        requirements=["短信验证码登录"],
        raw_text="支持短信验证码登录。",
    )


def _history(*cosines: float) -> list[dict[str, object]]:
    """检索回来的候选（带真实余弦）。判定只认 `vector_similarity`。"""
    return [
        {
            "requirement_key": f"REQ-{index:06d}",
            "requirement_name": f"历史需求{index}",
            "similarity": cosine,
            "vector_similarity": cosine,
        }
        for index, cosine in enumerate(cosines, start=1)
    ]


# 0.95 明显高出其余候选 → 落差 0.25 ≥ duplicate_contrast，判重复
DUPLICATE_LEVEL_HISTORY = _history(0.95, 0.70, 0.70, 0.70)


def test_hallucinated_duplicate_without_candidate_is_corrected() -> None:
    """模型谎报 duplicate=true 却一条候选都举不出来 → 降级。"""
    provider = FakeProvider(
        '{"duplicate": true, "related": false, "conflict": false, "independent": true, '
        '"reasoning": "自相矛盾", "candidates": []}'
    )
    result = AnalyzeSkill(provider=provider).analyze(_extracted(), historical_requirements=[])

    assert result.duplicate is False
    assert result.independent is True
    assert result.independent == (not (result.duplicate or result.related or result.conflict))


def test_fabricated_requirement_key_cannot_fake_similarity() -> None:
    """**模型无法再伪造相似度。**

    它自称 REQ-000001 相似度 0.99，但检索结果里**没有这条**（`historical_requirements`
    为空）。旧实现会照单全收这个 0.99 并据此保留 duplicate；现在查不到余弦 → `unverifiable`
    → 不足以支撑重复结论，降级。这是「不再采信模型自报的 similarity」最直接的一个后果。
    """
    provider = FakeProvider(
        '{"duplicate": true, "related": true, "conflict": false, "independent": false, '
        '"reasoning": "我编的", "candidates": [{"requirement_key": "REQ-000001", '
        '"title": "短信登录", "similarity": 0.99, "reason": "重复"}]}'
    )
    result = AnalyzeSkill(provider=provider).analyze(_extracted(), historical_requirements=[])

    assert result.duplicate is False
    assert result.candidates[0].level == "unverifiable"
    assert result.candidates[0].similarity_source == "keyword_only"


def test_confirmed_duplicate_with_real_evidence_is_kept() -> None:
    """模型判重复**且**它点名的候选在检索结果里确实达到重复级别 → 结论保留。

    与上一条对照：同样是「模型说重复 + 点名 REQ-000001」，差别只在于**证据存不存在**。
    """
    provider = FakeProvider(
        '{"duplicate": true, "related": true, "conflict": false, "independent": false, '
        '"reasoning": "重复", "candidates": [{"requirement_key": "REQ-000001", '
        '"title": "短信登录", "reason": "重复"}]}'
    )
    result = AnalyzeSkill(provider=provider).analyze(
        _extracted(), historical_requirements=DUPLICATE_LEVEL_HISTORY
    )

    assert result.duplicate is True
    assert result.independent is False
    top = result.candidates[0]
    assert top.level == "duplicate"
    assert top.similarity_source == "vector"
    assert top.similarity == 0.95, "相似度取的是检索回来的真实余弦"


def test_similarity_in_the_payload_is_ignored() -> None:
    """模型即使仍返回 similarity（提示词已不索要，但不能保证它不发），也一律忽略。"""
    provider = FakeProvider(
        '{"duplicate": true, "related": false, "conflict": false, "independent": false, '
        '"reasoning": "重复", "candidates": [{"requirement_key": "REQ-000004", '
        '"title": "历史需求4", "similarity": 0.999, "reason": "重复"}]}'
    )
    result = AnalyzeSkill(provider=provider).analyze(
        _extracted(), historical_requirements=DUPLICATE_LEVEL_HISTORY
    )

    top = result.candidates[0]
    assert top.similarity == 0.70, "取真实余弦，不是模型自报的 0.999"
    assert top.level != "duplicate", "0.70 达不到重复级别，模型的 duplicate 应被降级"
    assert result.duplicate is False


def test_supported_duplicate_is_upgraded_over_the_model() -> None:
    """**模型说不是重复，但候选确实到了重复级别 → 判重复。**

    ⚠️ 行为反转（这是第二次反转，两次的**理由不同**，值得都记下来）：

    1. 最初规则是「达到阈值就补判重复」，被撤掉 —— 因为当时的 `similarity` 只是模型
       回显检索分，那条规则等价于「向量分高就直接下判」，会推翻模型理由、产出
       「重复=是」配「理由：非重复」的矛盾卡片。撤掉是对的。
    2. B4 起级别由后端用**真实余弦 + 落差**算出，不再是模型回显，**第 1 条的理由不再成立**。
       而实测走查抓到：候选 `level=duplicate` 而结论是「独立」，审核人看到两张对不上的卡片。
       决策：`duplicate=true` 在本系统里不做任何自动动作（只把 next_action 置为
       manual_review，最终由人裁决），所以「够级别就标记出来」比「藏起来」更符合审核人利益。
    """
    provider = FakeProvider(
        '{"duplicate": false, "related": false, "conflict": false, "independent": true, '
        '"reasoning": "看走眼", "candidates": [{"requirement_key": "REQ-000001", '
        '"title": "短信登录", "reason": "高度相似"}]}'
    )
    result = AnalyzeSkill(provider=provider).analyze(
        _extracted(), historical_requirements=DUPLICATE_LEVEL_HISTORY
    )

    assert result.candidates[0].level == "duplicate", "前提：证据确实到了重复级别"
    assert result.duplicate is True, "证据够级别就判重复，不因模型反对而藏起来"
    assert result.independent is False


def test_upgrade_uses_level_not_the_payload_similarity() -> None:
    """升级的依据是**级别**，不是模型报的数字。弱候选不会因为模型吹了 0.99 就升级。"""
    provider = FakeProvider(
        '{"duplicate": false, "related": false, "conflict": false, "independent": true, '
        '"reasoning": "仅存在弱关联", "candidates": [{"requirement_key": "REQ-000004", '
        '"title": "历史需求4", "similarity": 0.99, "reason": "弱关联"}]}'
    )
    result = AnalyzeSkill(provider=provider).analyze(
        _extracted(), historical_requirements=DUPLICATE_LEVEL_HISTORY
    )

    assert result.candidates[0].level != "duplicate", "0.70 够不到重复级别"
    assert result.duplicate is False
    assert result.independent is True


def test_related_without_supporting_candidate_is_downgraded() -> None:
    """模型说关联，但点名的候选级别是 `none` → 降级。

    **实跑走查抓到的真事**：一条冷链需求被判 related=true，理由是「同属 workflow 域、
    共享异常—通知—留痕骨架」，而它点名的候选余弦全在噪声区间（level=none）。
    于是结论说「有关联」、候选面板却显示「低」—— 审核人看到的是一张自相矛盾的卡片。
    """
    provider = FakeProvider(
        '{"duplicate": false, "related": true, "conflict": false, "independent": false, '
        '"reasoning": "流程骨架相似", "candidates": [{"requirement_key": "REQ-000001", '
        '"title": "历史需求1", "reason": "同域"}]}'
    )
    # 全部候选挤在噪声带里：余弦差很小 → 落差极小，谁也够不到关联级别
    result = AnalyzeSkill(provider=provider).analyze(
        _extracted(), historical_requirements=_history(0.7588, 0.7010, 0.6638, 0.6602)
    )

    assert all(c.level == "none" for c in result.candidates)
    assert result.related is False, "没有候选证据支撑的关联结论必须降级"
    assert result.independent is True


def test_related_with_supporting_candidate_is_kept() -> None:
    """反过来：候选确实达到关联级别时，模型的 related=true 保留。"""
    provider = FakeProvider(
        '{"duplicate": false, "related": true, "conflict": false, "independent": false, '
        '"reasoning": "关联", "candidates": [{"requirement_key": "REQ-000001", '
        '"title": "历史需求1", "reason": "关联"}]}'
    )
    # 落差 0.10：过了关联闸门 0.0747，但没到重复闸门 0.1385 —— 正好落在 related 档
    result = AnalyzeSkill(provider=provider).analyze(
        _extracted(), historical_requirements=_history(0.85, 0.75, 0.75, 0.75)
    )

    assert result.candidates[0].level == "related"
    assert result.related is True
    assert result.duplicate is False
    assert result.independent is False


def test_parse_failure_falls_back_to_heuristic() -> None:
    provider = FakeProvider("不是 JSON 的一堆话")
    result = AnalyzeSkill(provider=provider).analyze(_extracted(), historical_requirements=[])
    # heuristic：无历史 → independent=True、duplicate=False
    assert result.duplicate is False
    assert result.independent is True
