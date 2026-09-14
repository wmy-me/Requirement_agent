"""需求库的筛选编译与 CSV 导出单测。

沿用仓库既有做法：手写 Fake 注入，不连数据库。
筛选部分只断言拼出的 SQL 片段与绑定参数（与 test_retrieval_filters.py 同风格）。
"""

import csv
import io

import pytest

from requirement_agent.application.requirement_service import RequirementService
from requirement_agent.infrastructure.db.repositories.requirement import RequirementMasterRepository

build_filter = RequirementMasterRepository._build_master_filter


def _row(**overrides) -> dict[str, object]:
    """一条 repository 聚合行的最小样本。"""
    row: dict[str, object] = {
        "id": 1,
        "requirement_key": "REQ-000001",
        "requirement_name": "登录增强",
        "final_requirement": "支持短信验证码登录",
        "current_version": 1,
        "status": "active",
        "lock_version": 0,
        "source_types": ["web"],
        "requester_names": ["张三"],
        "departments": ["用户中心"],
        "business_domains": ["auth"],
        "sensitivity_levels": ["internal"],
        "feature_contents": ["短信登录"],
        "feature_count": 1,
        "first_source_submitted_at": "2026-09-11 09:48:30",
        "latest_source_submitted_at": "2026-09-11 09:48:30",
    }
    row.update(overrides)
    return row


class FakeMasterRepo:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.calls: list[tuple[int, object]] = []

    def list_with_source_context(self, limit: int = 100, filters=None):
        self.calls.append((limit, filters))
        return self.rows


def _service(rows: list[dict[str, object]]) -> tuple[RequirementService, FakeMasterRepo]:
    repo = FakeMasterRepo(rows)
    return RequirementService(master_repo=repo), repo


# ── 筛选编译 ────────────────────────────────────────────────────────────


def test_empty_filters_produce_no_clauses() -> None:
    """空筛选不能产生任何额外子句——否则会改变既有列表接口的行为。"""
    where, having, params = build_filter({})
    assert (where, having, params) == ("", "", {})


def test_blank_values_are_ignored() -> None:
    where, having, params = build_filter({"q": "   ", "channel": "", "status": None})
    assert (where, having, params) == ("", "", {})


def test_where_keywords() -> None:
    where, having, params = build_filter({"status": "active", "has_version_ge": 2, "q": "登录"})

    assert "m.status = :status" in where
    assert "m.current_version >= :has_version_ge" in where
    assert "m.requirement_name ILIKE :q OR m.final_requirement ILIKE :q" in where
    assert having == ""  # 主表列的条件不该跑到 HAVING 里
    assert params["status"] == "active"
    assert params["has_version_ge"] == 2
    assert params["q"] == "%登录%"  # q 要包通配符


@pytest.mark.parametrize(
    "key,column",
    [
        ("channel", "s.source_type"),
        ("requester", "COALESCE(s.requester_name, s.requester_id)"),
        ("department", "COALESCE(s.metadata->>'department'"),
        ("business_domain", "COALESCE(s.metadata->>'business_domain'"),
        ("sensitivity_level", "COALESCE(s.metadata->>'sensitivity_level'"),
    ],
)
def test_having_keys_match_aggregate_values(key: str, column: str) -> None:
    """这些条件比的是聚合值，必须落在 HAVING（写进 WHERE 会把其它来源从行里抹掉）。"""
    where, having, params = build_filter({key: "x"})

    assert where == ""
    assert f"bool_or({column}" in having
    assert params[key] == "x"


def test_time_range_filters_cast_to_timestamptz() -> None:
    _where, having, params = build_filter(
        {"submitted_from": "2026-01-01", "submitted_to": "2026-12-31T23:59:59"}
    )

    assert "bool_or(s.submitted_at >= CAST(:submitted_from AS TIMESTAMPTZ))" in having
    assert "bool_or(s.submitted_at <= CAST(:submitted_to AS TIMESTAMPTZ))" in having
    assert params["submitted_from"] == "2026-01-01"


def test_multiple_having_conditions_are_conjoined() -> None:
    _where, having, _params = build_filter({"channel": "web", "department": "用户中心"})
    assert " AND " in having
    assert having.count("bool_or(") == 2


# ── CSV 导出 ────────────────────────────────────────────────────────────


def _parse_csv(text: str) -> list[list[str]]:
    assert text.startswith("﻿"), "缺少 BOM，Excel 打开中文会乱码"
    return list(csv.reader(io.StringIO(text.lstrip("﻿"))))


def test_csv_has_bom_and_chinese_header() -> None:
    service, _repo = _service([_row()])
    rows = _parse_csv(service.export_requirements_csv())

    assert rows[0] == [
        "需求编号", "需求名称", "最终需求", "状态", "业务领域", "当前版本", "功能数",
        "来源渠道", "来源人", "部门", "密级", "最近提交时间",
    ]
    assert rows[1][0] == "REQ-000001"
    assert len(rows) == 2


def test_csv_escapes_commas_quotes_and_newlines() -> None:
    """需求正文里出现逗号/引号/换行是常态，必须由 csv 模块转义。"""
    tricky = '含,逗号 与 "引号"\n以及换行'
    service, _repo = _service([_row(final_requirement=tricky, requirement_name=tricky)])

    rows = _parse_csv(service.export_requirements_csv())

    # 能原样解析回来，说明转义正确（表头 3 列分别是 名称/最终需求 的位置）
    assert rows[1][1] == tricky
    assert rows[1][2] == tricky


def test_csv_joins_multi_value_fields_with_ideographic_comma() -> None:
    service, _repo = _service(
        [_row(source_types=["web", "feishu"], requester_names=["张三", "李四"], departments=["a", "b"])]
    )

    row = _parse_csv(service.export_requirements_csv())[1]

    assert row[4] == "auth"          # 业务领域
    assert row[7] == "web、feishu"   # 来源渠道
    assert row[8] == "张三、李四"     # 来源人
    assert row[9] == "a、b"          # 部门


def test_csv_renders_missing_values_as_empty() -> None:
    service, _repo = _service([_row(departments=[], sensitivity_levels=[], latest_source_submitted_at=None)])
    row = _parse_csv(service.export_requirements_csv())[1]

    assert row[9] == ""    # 部门
    assert row[10] == ""   # 密级
    assert row[11] == ""   # 最近提交时间


def test_csv_passes_filters_and_limit_to_repository() -> None:
    service, repo = _service([_row()])
    service.export_requirements_csv(filters={"channel": "web"}, limit=42)

    assert repo.calls == [(42, {"channel": "web"})]


# ── 列表字段 ────────────────────────────────────────────────────────────


def test_list_keeps_all_multi_values_and_compat_field() -> None:
    """多值字段要返回全量而不是首元素——前端靠它聚合筛选下拉。"""
    service, _repo = _service([_row(business_domains=["auth", "report"])])

    item = service.list_requirements()[0]

    assert item["business_domains"] == ["auth", "report"]
    assert item["business_domain"] == "auth"  # 兼容字段仍取首个


def test_list_falls_back_to_general_when_no_domain() -> None:
    service, _repo = _service([_row(business_domains=[])])

    item = service.list_requirements()[0]

    assert item["business_domains"] == []
    assert item["business_domain"] == "general"
