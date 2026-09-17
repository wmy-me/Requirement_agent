"""雪花 ID 的序列化口径（T1）：响应里一律是**字符串**，且字符串能原样回传定位到同一行。

**为什么要专门测这个。** 雪花 ID 是 63 位正整数，`225548242094391297` 这种值超过 JS 的
`Number.MAX_SAFE_INTEGER`（2^53）——以 JSON number 发出去，前端 `JSON.parse` 会把它改成
`...296`（**差 1**）。而能不能被 double 精确表示，取决于 id 末尾有没有连续 5 个以上的 0，
也就是「同毫秒内有没有产生过第二个 id」，**不是可以依赖的性质**。

更早的一次事故就是这个形状：审核回传 `source_id` 后后端报 `not found` → 409。
所以这里不只断言类型，还要**真的走一遍「取出来 → 原样回传 → 命中同一行」**。

打真实库，用完自清理。
"""

from __future__ import annotations

import json
import os
import uuid

os.environ.setdefault("API_AUTH_TOKEN", "test-api-token")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from requirement_agent.api.app import app
from requirement_agent.common.snowflake import to_sid
from requirement_agent.infrastructure.db.session import SessionLocal

# B1 起 API 需要鉴权：`API_AUTH_TOKEN` 是兼容入口，等同于一个 admin token。
# 测具体的 401/403 行为请另建不带头的 TestClient（见 tests/integration/test_api_auth.py）。
client = TestClient(app, headers={"Authorization": "Bearer test-api-token"})


def _new_id() -> int:
    return uuid.uuid4().int % 9_000_000_000_000_000 + 1_000_000_000_000_000


# ── 纯函数：转换口径 ──────────────────────────────────────────────────────


def test_to_sid_is_idempotent_and_keeps_none() -> None:
    snowflake = 225548242094391297
    assert to_sid(snowflake) == "225548242094391297"
    assert to_sid("225548242094391297") == "225548242094391297"  # 幂等
    assert to_sid(None) is None
    assert to_sid("") is None
    assert to_sid("   ") is None


def test_to_sid_fixes_the_value_that_double_cannot_hold() -> None:
    """**这就是要防的那一个**：真实库里的 `outbox_event.id`。"""
    snowflake = 225548242094391297
    assert int(float(snowflake)) != snowflake, "这个 id 本来就该是丢精度的反例"
    assert int(to_sid(snowflake)) == snowflake, "字符串化之后必须逐位保真"


# ── 真实：响应里的 ID 类型 ────────────────────────────────────────────────

# （端点, 取哪个列表, 该列表里哪些字段是 id）
ID_ENDPOINTS = [
    ("/api/v1/requirements/REQ-000015/versions", "items", ("id", "requirement_id")),
    ("/api/v1/requirements/REQ-000015/features", "items", ("id", "requirement_id", "origin_source_id")),
    ("/api/v1/requirements/REQ-000015/capabilities", "capabilities", ("feature_id", "capability_id")),
    ("/api/v1/requirements/REQ-000015/relations", "items", ("id", "source_id")),
    ("/api/v1/capabilities", "items", ("id", "origin_source_id")),
    ("/api/v1/reviews/pending", "items", ("source_id",)),
]


@pytest.mark.parametrize("path,items_key,id_fields", ID_ENDPOINTS)
def test_ids_in_responses_are_strings(path, items_key, id_fields) -> None:
    """所有雪花 ID 字段必须是字符串 —— 不是「大数才转」，是一律。"""
    response = client.get(path)
    assert response.status_code == 200, response.text
    items = response.json().get(items_key) or []
    if not items:
        pytest.skip(f"{path} 当前无样本数据")

    for field in id_fields:
        value = items[0].get(field)
        if value is None:
            continue
        assert isinstance(value, str), f"{path} 的 `{field}` 是 {type(value).__name__}，应为 str"


def test_provenance_source_ids_are_strings() -> None:
    """嵌套在 JSONB 里的 ID 也要转 —— 那是「同一字段两种形状」最容易漏的地方。"""
    response = client.get("/api/v1/requirements/REQ-000015/features")
    items = response.json()["items"]
    seen = [
        entry.get("source_id")
        for item in items
        for entry in (item.get("provenance") or [])
    ]
    if not seen:
        pytest.skip("没有带 provenance 的样本")
    assert all(value is None or isinstance(value, str) for value in seen), seen


def test_diff_payload_source_id_is_a_string() -> None:
    """`diff_payload` 是**存储型 JSON**，读时才转 —— 老行也必须一致。"""
    response = client.get("/api/v1/requirements/REQ-000015/versions")
    payloads = [item.get("diff_payload") or {} for item in response.json()["items"]]
    values = [p.get("source_id") for p in payloads if "source_id" in p]
    if not values:
        pytest.skip("没有带 source_id 的 diff_payload")
    assert all(isinstance(value, str) for value in values), values


# ── 端到端：字符串能原样回传并定位到同一行 ────────────────────────────────


class _IdFixture:
    """一条需求 + 一个能力关联（`proposed`），用来走一次真实的裁决往返。"""

    def __init__(self) -> None:
        self.tag = uuid.uuid4().hex[:8]
        self.master_id = _new_id()
        self.key = f"REQ-SID-{self.tag}"
        self.feature_id = _new_id()
        self.capability_id = _new_id()
        with SessionLocal() as session:
            session.execute(
                text(
                    "INSERT INTO requirement_master (id, requirement_key, requirement_name, "
                    "final_requirement, current_version, status, lock_version) "
                    "VALUES (:id, :k, :n, 'x', 1, 'active', 1)"
                ),
                {"id": self.master_id, "k": self.key, "n": f"ID走查-{self.tag}"},
            )
            session.execute(
                text(
                    "INSERT INTO requirement_feature (id, requirement_id, feature_key, content, "
                    "status, ordinal, origin_source_id, origin_requirement_key, origin_version_no, "
                    "provenance, content_hash) "
                    "VALUES (:id, :rid, 'F-001', '支持导出', 'active', 1, NULL, :rk, 1, "
                    "CAST('[]' AS JSONB), 'h')"
                ),
                {"id": self.feature_id, "rid": self.master_id, "rk": self.key},
            )
            session.execute(
                text(
                    "INSERT INTO capability (id, action, object, display_name, status, created_by) "
                    "VALUES (:id, '导出', 'Excel', :d, 'active', 'test')"
                ),
                {"id": self.capability_id, "d": f"导出Excel-{self.tag}"},
            )
            session.execute(
                text(
                    "INSERT INTO feature_capability (feature_id, capability_id, raw_text, review_status) "
                    "VALUES (:f, :c, '支持导出', 'proposed')"
                ),
                {"f": self.feature_id, "c": self.capability_id},
            )
            session.commit()

    def cleanup(self) -> None:
        with SessionLocal() as session:
            session.execute(
                text("DELETE FROM feature_capability WHERE capability_id = :c"), {"c": self.capability_id}
            )
            session.execute(
                text("DELETE FROM requirement_feature WHERE requirement_id = :id"), {"id": self.master_id}
            )
            session.execute(text("DELETE FROM requirement_master WHERE id = :id"), {"id": self.master_id})
            session.execute(text("DELETE FROM capability WHERE id = :c"), {"c": self.capability_id})
            session.commit()


def test_string_id_survives_a_full_round_trip() -> None:
    """**这条才是 T1 的真正验收**：取出的 id 原样（字符串）回传，必须命中同一行。

    若前端按老习惯 `Number()` 一下再发，遇到低位不为 0 的 id 就会打到别的行 ——
    这里用 fixture 造一个**末尾没有多余 0 的** id，把那个失败模式钉死。
    """
    fixture = _IdFixture()
    try:
        # 造一个「低位不为 0」的 id 场景：直接用 fixture 的 id（它是随机数，多半低位非 0）
        listed = client.get(f"/api/v1/requirements/{fixture.key}/capabilities").json()["capabilities"]
        assert listed, "fixture 没造出能力关联"
        row = listed[0]
        assert isinstance(row["feature_id"], str) and isinstance(row["capability_id"], str)

        # 原样回传（**不做任何数值转换**）
        response = client.patch(
            "/api/v1/feature-capabilities",
            json={
                "feature_id": row["feature_id"],
                "capability_id": row["capability_id"],
                "status": "confirmed",
            },
        )
        assert response.status_code == 200, response.text

        with SessionLocal() as session:
            status = session.execute(
                text(
                    "SELECT review_status FROM feature_capability "
                    "WHERE feature_id = :f AND capability_id = :c"
                ),
                {"f": fixture.feature_id, "c": fixture.capability_id},
            ).scalar_one()
        assert status == "confirmed", "字符串 id 没能定位到同一行"
    finally:
        fixture.cleanup()


def test_number_round_trip_would_have_missed_a_low_bit_id() -> None:
    """反证：同一个 id 若被当成 JS number 处理，就会变成**另一个**值。

    这不是实现测试，是把「为什么不能 `Number()` 回去」写成可执行的断言 ——
    免得将来有人觉得那两个 `Number()` 是多余的顺手加回来。
    """
    low_bit_id = 225548242094391297
    as_js_number = int(float(low_bit_id))
    assert as_js_number != low_bit_id
    assert json.loads(json.dumps(low_bit_id)) == low_bit_id  # Python 侧不丢
    # 前端拿到的是字符串，逐位保真
    assert int(to_sid(low_bit_id)) == low_bit_id
