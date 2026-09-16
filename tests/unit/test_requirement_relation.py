"""需求关系边的生成规则。

关系由分析结果翻译而来，三条规则缺一不可：
1. 对应布尔量为真（duplicate/related/conflict）；
2. **该候选自己的相似度**过阈值——整份分析的布尔量是「至少有一个候选命中」，
   不能据此把每个候选都标上关系；
3. 候选的 requirement_key 必须查得到已存在的 REQ（检索候选里混着尚未成为正式需求的条目）。
"""

from requirement_agent.workflows.commit_nodes import _analysis_relations

SUBJECT = "REQ-000010"


class FakeMaster:
    def __init__(self, key: str, id_: int) -> None:
        self.requirement_key = key
        self.id = id_


class FakeMasterRepo:
    def __init__(self, keys: tuple[str, ...]) -> None:
        self._by_key = {key: FakeMaster(key, index) for index, key in enumerate(keys, start=1)}

    def get_by_key(self, requirement_key: str, session=None):
        return self._by_key.get(requirement_key)


def _analysis(**overrides) -> dict:
    data: dict = {"duplicate": False, "related": False, "conflict": False, "candidates": []}
    data.update(overrides)
    return data


def _candidate(key: str, similarity: float, reason: str = "理由") -> dict:
    return {"requirement_key": key, "similarity": similarity, "reason": reason}


def _run(analysis: dict, keys: tuple[str, ...] = ("REQ-000001", "REQ-000002")) -> list[dict]:
    return _analysis_relations(
        analysis, master_repo=FakeMasterRepo(keys), session=None, exclude_key=SUBJECT
    )


def test_independent_analysis_produces_no_relations() -> None:
    """判为独立时不产生任何关系，即便候选列表不为空。"""
    analysis = _analysis(candidates=[_candidate("REQ-000001", 0.95)])

    assert _run(analysis) == []


def test_duplicate_above_threshold_becomes_duplicates_of() -> None:
    analysis = _analysis(duplicate=True, candidates=[_candidate("REQ-000001", 0.92)])

    relations = _run(analysis)

    assert len(relations) == 1
    assert relations[0]["relation_type"] == "duplicates_of"
    assert relations[0]["target_requirement_key"] == "REQ-000001"
    assert relations[0]["target_requirement_id"] == 1
    assert relations[0]["similarity"] == 0.92
    assert relations[0]["reason"] == "理由"


def test_related_above_threshold_becomes_related() -> None:
    analysis = _analysis(related=True, candidates=[_candidate("REQ-000002", 0.78)])

    relations = _run(analysis)

    assert [r["relation_type"] for r in relations] == ["related"]


def test_weak_candidate_is_skipped() -> None:
    """related=True 是「至少有一个命中」，不能让 0.60 的弱候选也变成「关联」。"""
    analysis = _analysis(
        related=True,
        candidates=[_candidate("REQ-000001", 0.60), _candidate("REQ-000002", 0.78)],
    )

    relations = _run(analysis)

    assert [r["target_requirement_key"] for r in relations] == ["REQ-000002"]


def test_candidate_without_existing_requirement_is_skipped() -> None:
    """检索候选可能是还没成为正式需求的来源——写进去就是悬空关系。"""
    analysis = _analysis(related=True, candidates=[_candidate("REQ-999999", 0.90)])

    assert _run(analysis) == []


def test_subject_key_is_excluded() -> None:
    """合并进既有 REQ 时，主体的 key 可能与候选重复，别把自己连到自己。"""
    analysis = _analysis(duplicate=True, candidates=[_candidate(SUBJECT, 0.95)])

    assert _run(analysis) == []


def test_conflict_is_recorded_independently() -> None:
    """冲突不是从相似度推出来的（是标签启发式），与 duplicate/related 互不排斥。"""
    analysis = _analysis(
        duplicate=True,
        conflict=True,
        candidates=[_candidate("REQ-000001", 0.90)],
    )

    relations = _run(analysis)

    assert sorted(r["relation_type"] for r in relations) == ["conflict", "duplicates_of"]


def test_conflict_survives_low_similarity() -> None:
    """冲突不看相似度：低相似的候选也可以是冲突。"""
    analysis = _analysis(conflict=True, candidates=[_candidate("REQ-000001", 0.10)])

    assert [r["relation_type"] for r in _run(analysis)] == ["conflict"]


def test_non_numeric_similarity_is_treated_as_zero() -> None:
    """模型偶尔给出 "高" 这类非数值——按 0 处理而不是抛异常。"""
    analysis = _analysis(related=True, candidates=[{"requirement_key": "REQ-000001", "similarity": "高"}])

    assert _run(analysis) == []


# ── 合并即确认（E 批）──────────────────────────────────────────────────
#
# `_merge_confirmations` 决定「人工合并时，要不要替人确认若干条重复关系」。
# 核心是一条保守规则：**合并目标本身必须就是分析认定的重复对象**，
# 否则一条都不确认 —— 人工合并不一定是因为重复，也可能只是想并入。


def _merge(analysis: dict, merge_key: str, keys: tuple[str, ...] = ("REQ-000001", "REQ-000002")):
    from requirement_agent.workflows.commit_nodes import _merge_confirmations

    return _merge_confirmations(
        analysis, master_repo=FakeMasterRepo(keys), session=None, merge_key=merge_key
    )


def test_merge_confirms_other_duplicate_candidates() -> None:
    """合并目标就是重复对象 → 与它**其它**重复候选的边被确认。"""
    analysis = _analysis(
        duplicate=True,
        candidates=[
            _candidate("REQ-000001", 0.92),  # 合并目标自己
            _candidate("REQ-000002", 0.88),  # 另一条重复候选 → 应被确认
        ],
    )

    confirmations = _merge(analysis, "REQ-000001")

    assert [c["target_requirement_key"] for c in confirmations] == ["REQ-000002"]
    assert all(c["relation_type"] == "duplicates_of" for c in confirmations)


def test_merge_target_absent_from_candidates_confirms_nothing() -> None:
    """**核心断言**：合并目标不是重复候选 → 一条都不确认。"""
    analysis = _analysis(
        duplicate=True,
        candidates=[_candidate("REQ-000002", 0.95)],  # 只有别人重复，目标自己不在里面
    )

    assert _merge(analysis, "REQ-000001") == []


def test_merge_without_duplicate_flag_confirms_nothing() -> None:
    """分析没判重复时，合并不该凭空确认重复关系。"""
    analysis = _analysis(
        duplicate=False,
        candidates=[_candidate("REQ-000001", 0.99), _candidate("REQ-000002", 0.99)],
    )

    assert _merge(analysis, "REQ-000001") == []


def test_merge_ignores_candidates_below_duplicate_threshold() -> None:
    """只有过 duplicate 阈值的候选才被确认 —— 弱候选不该借合并混进 confirmed。"""
    analysis = _analysis(
        duplicate=True,
        candidates=[
            _candidate("REQ-000001", 0.95),  # 目标自己，过线
            _candidate("REQ-000002", 0.61),  # 远低于 duplicate 阈值
        ],
    )

    assert _merge(analysis, "REQ-000001") == []


def test_merge_never_confirms_a_self_loop() -> None:
    """目标 REQ 自己不能出现在确认列表里（合并时 subject 就是它，会变成自环）。"""
    analysis = _analysis(
        duplicate=True,
        candidates=[_candidate("REQ-000001", 0.95), _candidate("REQ-000002", 0.90)],
    )

    keys = [c["target_requirement_key"] for c in _merge(analysis, "REQ-000001")]

    assert "REQ-000001" not in keys


def test_merge_skips_candidates_without_existing_requirement() -> None:
    """候选指向还没成为正式需求的条目 → 跳过，不能写悬空关系。"""
    analysis = _analysis(
        duplicate=True,
        candidates=[_candidate("REQ-000001", 0.95), _candidate("REQ-999999", 0.90)],
    )

    assert _merge(analysis, "REQ-000001") == []


def test_merge_only_touches_duplicates_not_related_or_conflict() -> None:
    """related / conflict 不该被合并顺带确认 —— 只有重复才会促成合并。"""
    analysis = _analysis(
        duplicate=True,
        related=True,
        conflict=True,
        candidates=[_candidate("REQ-000001", 0.95), _candidate("REQ-000002", 0.90)],
    )

    confirmations = _merge(analysis, "REQ-000001")

    assert {c["relation_type"] for c in confirmations} == {"duplicates_of"}
