"""需求模块（D 批）：抽取的模块结构一路落到 requirement_feature 的 module 列。

链路：抽取输出 `modules: [{module, items}]` → commit_nodes._module_lines 展平成
带模块标签的行 → create_features 落 module_key/module_name。
"""

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from requirement_agent.infrastructure.db.repositories.review import RequirementFeatureRepository
from requirement_agent.infrastructure.db.session import engine
from requirement_agent.workflows.commit_nodes import _module_lines


# ── _module_lines（纯函数）──────────────────────────────────────────────


class _FakeSource:
    def __init__(self, modules) -> None:
        self.metadata = {"extracted": {"modules": modules}}


def test_module_lines_expands_modules_with_labels() -> None:
    source = _FakeSource(
        [
            {"module": "登录", "items": ["短信验证码登录", "账号锁定策略"]},
            {"module": "报表", "items": ["按部门导出"]},
        ]
    )

    lines = _module_lines(source)

    assert lines == [
        {"content": "短信验证码登录", "module_key": "登录", "module_name": "登录"},
        {"content": "账号锁定策略", "module_key": "登录", "module_name": "登录"},
        {"content": "按部门导出", "module_key": "报表", "module_name": "报表"},
    ]


def test_module_lines_empty_when_no_modules() -> None:
    assert _module_lines(_FakeSource(None)) == []
    assert _module_lines(_FakeSource([])) == []
    source = _FakeSource([{"module": "登录", "items": []}])  # 模块但无条目
    assert _module_lines(source) == []


# ── create_features 落库（真实 DB，事务回滚）────────────────────────────


def test_create_features_persists_module_columns() -> None:
    """dict 行（带模块标签）落库后 module_key/module_name 要真的写进 requirement_feature。"""
    conn = engine.connect()
    trans = conn.begin()
    try:
        session = Session(bind=conn, join_transaction_mode="create_savepoint")
        repo = RequirementFeatureRepository()

        master_id = session.execute(
            text(
                "INSERT INTO requirement_master (id, requirement_key, requirement_name, final_requirement, "
                "current_version, status, lock_version) "
                "VALUES (100000000000000001, 'REQ-MOD', '模块测试', 'x', 1, 'active', 1) "
                "ON CONFLICT (requirement_key) DO UPDATE SET requirement_name = EXCLUDED.requirement_name "
                "RETURNING id"
            )
        ).scalar()

        features = repo.create_features(
            master_id,
            [
                {"content": "短信验证码登录", "module_key": "登录", "module_name": "登录"},
                {"content": "按部门导出", "module_key": "报表", "module_name": "报表"},
                "不归属模块的功能",
            ],
            source_id=None,
            requirement_key="REQ-MOD",
            version_no=1,
            session=session,
        )

        by_content = {f["content"]: f for f in features}
        assert by_content["短信验证码登录"]["module_key"] == "登录"
        assert by_content["短信验证码登录"]["module_name"] == "登录"
        assert by_content["按部门导出"]["module_name"] == "报表"
        assert by_content["不归属模块的功能"]["module_key"] is None  # 扁平行不归属任何模块

        # 直接查库确认列真写进去了
        rows = session.execute(
            text(
                "SELECT content, module_key, module_name FROM requirement_feature WHERE requirement_id = :id"
            ),
            {"id": master_id},
        ).mappings().all()
        by_db = {r["content"]: r for r in rows}
        assert by_db["短信验证码登录"]["module_key"] == "登录"
        assert by_db["按部门导出"]["module_name"] == "报表"
    finally:
        trans.rollback()
        conn.close()
    print("（事务已回滚，未留数据）")
