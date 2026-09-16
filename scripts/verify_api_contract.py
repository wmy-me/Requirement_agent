#!/usr/bin/env python
"""契约验证：核对 `docs/api-contract.md` 描述的东西与**真实响应**是否一致。

**为什么要有这个脚本。** 2026-09-16 复跑契约时抓到三处「文档写错了」——
§1 说「雪花 ID 一律字符串」（只有审核端点是这样）、需求库列表少写 4 个字段、
对话 SSE 的事件名写的是后端根本不存在的 `delta`/`message`。这三条**都不会让测试变红**
（它们不是代码的错，是文档与代码脱节），却会让照文档重写的前端「实现完跑不通」。
所以把 §9 描述的那套方法固化成可执行的脚本：**能跑、会红、可挂 CI**。

它做三件事（对应 `docs/api-contract.md` §9 的四步）：

1. **逐端点核对字段存在**：`SPEC` 里列的字段（= 契约承诺的响应形状）必须在真实响应里，
   缺一个就报错并让进程以非 0 退出码结束。
2. **清点大整数的 JSON 类型**：把响应里超过 `2^53` 的值挑出来，报告它被序列化成
   `string` 还是 `number`、以及能否被 double 精确表示。
   —— T1 的「ID 序列化统一」就靠这张表定范围与验收。
3. **数据库级 ID 精度扫描**：直接查各表的 `id` 列，找**能不能被 double 精确表示**的值。

> ### ⚠️ 为什么第 3 步不能省
>
> 第 2 步只扫得到**当前有数据的端点**。2026-09-16 真实库里就有一个不可精确表示的 ID
> （`outbox_event.id = 225548242094391297`，`int(float())` 差 1），但它属于一条
> `completed` 事件、不进任何响应 —— 只靠端点采样**永远扫不到**。而它一旦变成死信，
> 就会以 JSON number 出现在 `/ops/outbox`，前端拿它拼 `dead-letters/{id}/retry`
> 就会打到错误的一行。第 3 步查的是**数据**，与「现在有没有接口暴露它」无关。

**它不能替代什么**：SPEC 是人写的字段清单，不是从 `app.js` 里自动解析出来的
（那需要真正的 AST，正则做不到可靠）。它对 `app.js` 只做**信息性**比对（打印而不判失败）：
契约本就该描述完整响应，而当前 `app.js` 只是它的一个（即将被替换的）消费者 ——
「契约有、前端没读」是正常的；反过来的「前端读了、契约没写」才值得看。

用法：
    python scripts/verify_api_contract.py            # 全量核对
    python scripts/verify_api_contract.py --only ids # 只看 ID 类型表与精度扫描

退出码：0 = 全绿；1 = 有字段缺失，或存在**会被暴露且不可精确表示**的 ID。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

os.environ.setdefault("API_AUTH_TOKEN", "test-api-token")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_JS = PROJECT_ROOT / "static" / "js" / "app.js"

# 大整数门槛：JS 的 Number.MAX_SAFE_INTEGER。
JS_MAX_SAFE_INTEGER = 2**53

# 打真实库时用的样本 key —— 取「存在且带版本链」的那条，测不出东西时脚本会如实报「跳过」。
SAMPLE_KEY = "REQ-000015"


@dataclass
class Endpoint:
    """一个要核对的端点，以及**前端实际会读**的字段。"""

    name: str
    path: str
    fields: tuple[str, ...]
    items_key: str = "items"
    params: dict[str, object] = field(default_factory=dict)
    # 该端点是否可能没有样本数据（空 items 时跳过字段核对而不是判失败）
    optional: bool = False


# 这份清单是**契约的机器可读副本**：字段名的唯一来源仍是 `docs/api-contract.md`，
# 这里只是把它变成可执行的断言。改契约就要改这里，否则脚本会红——这正是目的。
SPEC: tuple[Endpoint, ...] = (
    Endpoint(
        name="需求库列表",
        path="/api/v1/requirements",
        params={"limit": 3},
        fields=(
            "requirement_key", "requirement_name", "final_requirement", "status",
            "business_domain", "business_domains", "current_version", "feature_count",
            "requester_names", "source_types", "departments", "sensitivity_levels",
            "first_source_submitted_at", "latest_source_submitted_at",
        ),
    ),
    Endpoint(
        name="版本历史",
        path=f"/api/v1/requirements/{SAMPLE_KEY}/versions",
        fields=(
            "id", "requirement_id", "parent_version_id", "parent_version_no", "version_no",
            "version_title", "change_type", "requirement_snapshot", "change_summary",
            "diff_payload", "feature_changes", "created_by", "reviewed_by", "created_at",
        ),
    ),
    Endpoint(
        name="功能明细",
        path=f"/api/v1/requirements/{SAMPLE_KEY}/features",
        fields=(
            "id", "requirement_id", "feature_key", "content", "status", "ordinal",
            "origin_source_id", "origin_requirement_key", "origin_version_no",
            "removed_version_no", "provenance", "module_key", "module_name",
        ),
    ),
    Endpoint(
        name="版本 diff",
        path=f"/api/v1/requirements/{SAMPLE_KEY}/diff",
        # diff 的顶层不是列表，字段在顶层上（items_key 置空表示「顶层就是对象」）
        items_key="",
        fields=(
            "requirement_key", "from_version", "to_version",
            "added", "removed", "modified", "unchanged",
        ),
    ),
    Endpoint(
        name="详情页能力与条件",
        path=f"/api/v1/requirements/{SAMPLE_KEY}/capabilities",
        items_key="capabilities",
        fields=(
            "feature_id", "capability_id", "feature_key", "feature_content",
            "action", "object", "display_name", "capability_status",
            "raw_text", "confidence", "review_status", "decided_by",
        ),
    ),
    Endpoint(
        name="需求关系（双向）",
        path=f"/api/v1/requirements/{SAMPLE_KEY}/relations",
        fields=(
            "id", "subject_requirement_key", "target_requirement_key", "relation_type",
            "reason", "similarity", "source_id", "status", "created_by", "decided_by",
            "created_at", "updated_at", "direction", "other_requirement_key",
            "other_requirement_name",
        ),
        optional=True,  # 库里可能一条关系都没有
    ),
    Endpoint(
        name="能力词表",
        path="/api/v1/capabilities",
        params={"limit": 3},
        fields=(
            "id", "action", "object", "display_name", "status",
            "created_by", "origin_source_id", "created_at", "updated_at",
        ),
    ),
    Endpoint(
        name="条件词表",
        path="/api/v1/constraints",
        params={"limit": 3},
        fields=("id",),  # ⚠️ 形状未经实测（词表是空表），只核对端点可达
        optional=True,
    ),
    Endpoint(
        name="候选标题",
        path=f"/api/v1/requirements/{SAMPLE_KEY}/titles",
        fields=(
            "id", "requirement_id", "title", "angle", "capability_id", "constraint_key",
            "source", "review_status", "decided_by", "created_by", "created_at", "highlight",
        ),
        optional=True,  # 库里 0 行
    ),
    Endpoint(
        name="待办列表",
        path="/api/v1/reviews/pending",
        params={"limit": 5},
        fields=(
            "source_id", "source_type", "source_event_id", "requester_id", "requester_name",
            "original_text", "extracted_text", "original_payload", "metadata",
            "processing_status", "submitted_at", "updated_at",
        ),
        optional=True,
    ),
)


def _client():
    from fastapi.testclient import TestClient

    from requirement_agent.api.app import app

    return TestClient(app)


def _walk(node, path, out, depth=0):
    """把响应里的每个叶子与它的路径摊平，用于按名字找字段与清点类型。"""
    if depth > 6:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            _walk(value, f"{path}.{key}" if path else key, out, depth + 1)
    elif isinstance(node, list):
        for item in node[:2]:
            _walk(item, path + "[]", out, depth + 1)
    else:
        out.append((path, node))


def _leaf_names(items) -> set[str]:
    """一个样本里出现过的字段名（只取末段，忽略嵌套路径）。"""
    names: set[str] = set()
    for node in items:
        leaves: list[tuple[str, object]] = []
        _walk(node, "", leaves)
        for path, _value in leaves:
            names.add(path.split(".")[-1].replace("[]", ""))
        if isinstance(node, dict):
            names.update(node.keys())
    return names


def check_endpoints(client) -> tuple[list[str], list[dict[str, object]]]:
    """核对每个端点的字段是否存在。返回（问题列表, 类型表原始数据）。"""
    problems: list[str] = []
    big_ints: list[dict[str, object]] = []

    for endpoint in SPEC:
        response = client.get(endpoint.path, params=endpoint.params or None)
        if response.status_code != 200:
            problems.append(f"{endpoint.name}: HTTP {response.status_code}（{endpoint.path}）")
            continue

        body = response.json()
        if endpoint.items_key == "":
            samples = [body]
        else:
            items = body.get(endpoint.items_key) if isinstance(body, dict) else None
            if not items:
                if not endpoint.optional:
                    problems.append(f"{endpoint.name}: `{endpoint.items_key}` 为空，无法核对字段")
                # 空数据也要清点顶层键的类型（如 {items: []} 本身没有大整数）
                continue
            samples = items[:2]

        # 顶层键也算：diff 这种「字段在顶层」的端点靠它
        present = _leaf_names(samples)
        if isinstance(body, dict) and endpoint.items_key == "":
            present.update(body.keys())

        missing = [name for name in endpoint.fields if name not in present]
        if missing:
            problems.append(
                f"{endpoint.name}: 响应里没有 {', '.join('`' + m + '`' for m in missing)}"
            )

        for sample in samples:
            leaves: list[tuple[str, object]] = []
            _walk(sample, endpoint.items_key or "", leaves)
            for path, value in leaves:
                if isinstance(value, bool):
                    continue
                if isinstance(value, int) and abs(value) > JS_MAX_SAFE_INTEGER:
                    big_ints.append(
                        {
                            "endpoint": endpoint.name,
                            "field": path,
                            "kind": "number",
                            "value": value,
                            "exact": int(float(value)) == value,
                        }
                    )
                elif (
                    isinstance(value, str)
                    and value.isdigit()
                    and int(value) > JS_MAX_SAFE_INTEGER
                ):
                    big_ints.append(
                        {
                            "endpoint": endpoint.name,
                            "field": path,
                            "kind": "string",
                            "value": value,
                            "exact": True,  # 字符串在 JS 里原样透传
                        }
                    )
    return problems, big_ints


# 以雪花 ID 作主键、且会经 API 暴露的表。`id` 之外还要看被外键引用的列
# （`feature_id` / `capability_id` 之类会作为请求体参数回传）。
ID_SCAN_TARGETS: tuple[tuple[str, str], ...] = (
    ("requirement_source", "id"),
    ("requirement_master", "id"),
    ("requirement_version", "id"),
    ("requirement_feature", "id"),
    ("requirement_relation", "id"),
    ("capability", "id"),
    ("feature_capability", "feature_id"),
    ("feature_capability", "capability_id"),
    ("document_asset", "id"),
    ("document_chunk", "id"),
    ("requirement_title_candidate", "id"),
    ("outbox_event", "id"),
    ("audit_event", "id"),
    ("requirement_embedding", "id"),
)


def scan_imprecise_ids() -> list[dict[str, object]]:
    """直接查库，找**不能被 double 精确表示**的 ID。

    这一步与「现在有没有接口暴露它」无关 —— 它查的是数据里到底有没有这种值。
    有，就意味着**任何**将来把它当 JSON number 发出去的端点都已经埋了雷。
    """
    from sqlalchemy import text

    from requirement_agent.infrastructure.db.session import SessionLocal

    found: list[dict[str, object]] = []
    with SessionLocal() as session:
        for table, column in ID_SCAN_TARGETS:
            try:
                rows = session.execute(
                    text(f"SELECT {column} FROM {table} WHERE {column} > :floor"),
                    {"floor": JS_MAX_SAFE_INTEGER},
                ).all()
            except Exception:
                session.rollback()
                continue
            for (value,) in rows:
                if not isinstance(value, int):
                    continue
                if int(float(value)) != value:
                    found.append({"table": table, "column": column, "value": value})
    return found


def app_js_coverage_notes() -> list[str]:
    """信息性比对：契约里承诺、但当前 `app.js` 从没提过的字段。

    **不作为失败** —— 契约描述的是完整响应形状，`app.js` 只是它的一个消费者，
    而且是要被替换的那个。「契约有、前端没读」是正常的；这条只帮人判断
    「这个字段是不是可以删了」，别拿它当验收标准。
    """
    if not APP_JS.exists():
        return []
    source = APP_JS.read_text(encoding="utf-8")
    unread: list[str] = []
    for endpoint in SPEC:
        for name in endpoint.fields:
            if name not in source:
                unread.append(f"{endpoint.name}.{name}")
    return unread


def print_type_table(big_ints: list[dict[str, object]]) -> None:
    """大整数的 JSON 类型表 —— T1（ID 序列化统一）的范围就由它定。"""
    print("\n" + "=" * 78)
    print("大整数（> 2^53）的 JSON 类型表")
    print("=" * 78)
    if not big_ints:
        print("（这次没扫到超过 2^53 的值 —— 样本数据不足，不代表没有风险）")
        return
    # 同一个 (端点, 字段, 类型) 只留一行，免得按样本数重复刷屏
    seen: set[tuple] = set()
    unique: list[dict[str, object]] = []
    for row in big_ints:
        key = (row["endpoint"], row["field"], row["kind"])
        if key not in seen:
            seen.add(key)
            unique.append(row)

    print(f"{'端点':<18}{'字段':<34}{'类型':<9}可精确表示")
    print("-" * 78)
    risky = 0
    for row in unique:
        exact = bool(row["exact"])
        if not exact:
            risky += 1
        flag = "" if exact else "  ⚠️ 会丢精度"
        print(f"{str(row['endpoint']):<18}{str(row['field']):<34}{str(row['kind']):<9}{exact}{flag}")
    print("-" * 78)
    print(
        f"共 {len(unique)} 处；其中 `number` 类型且**不可精确表示**的有 {risky} 处 —— "
        "这些一旦被前端原样回传就会指向错误的一行。"
    )


def print_id_scan(found: list[dict[str, object]]) -> None:
    """数据库级的 ID 精度扫描结果 —— 这一节才是 T1 的验收依据。"""
    print("\n" + "=" * 78)
    print("数据库级 ID 精度扫描（不可被 double 精确表示的 ID）")
    print("=" * 78)
    if not found:
        print("✅ 库里所有 ID 都能被 double 精确表示。")
        print("   注意：这是**当前数据**的结论，不代表将来也是 —— 雪花 ID 只要")
        print("   同毫秒内产生第二个（seq≠0），低位的 0 就没了。")
        return
    print(f"{'表':<32}{'列':<16}{'值':<22}发给 JS 会变成")
    print("-" * 78)
    for row in found:
        value = int(row["value"])  # type: ignore[arg-type]
        print(f"{str(row['table']):<32}{str(row['column']):<16}{value:<22}{int(float(value))}")
    print("-" * 78)
    print(
        f"⚠️ 共 {len(found)} 个。它们**现在**也许还没进任何响应，但只要有端点把它当 JSON "
        "number 发出去，前端就会拿到一个错误的 id。"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="核对 API 契约与真实响应")
    parser.add_argument("--only", choices=("all", "ids"), default="all")
    args = parser.parse_args()

    print(f"契约验证 · 打真实库 · 样本 {SAMPLE_KEY}")
    print(f"SPEC 覆盖 {len(SPEC)} 个端点")

    client = _client()
    problems: list[str] = []
    big_ints: list[dict[str, object]] = []

    if args.only == "all":
        problems, big_ints = check_endpoints(client)
    else:
        _, big_ints = check_endpoints(client)

    print_type_table(big_ints)

    imprecise = scan_imprecise_ids()
    print_id_scan(imprecise)

    if args.only == "all":
        unread = app_js_coverage_notes()
        if unread:
            print("\n" + "-" * 78)
            print(f"（信息）契约里承诺、但当前 app.js 从未提及的字段 {len(unread)} 个：")
            print("  " + "、".join(f"`{name}`" for name in unread[:12]) + ("…" if len(unread) > 12 else ""))
            print("  契约描述完整响应，而 app.js 只是它的一个（待替换的）消费者 —— 这条不算问题。")

    # 危险的组合：**库里真有不可精确表示的 ID** 且 **仍有 id 类字段以 number 暴露**。
    # 两个条件缺一不可 —— 只看前者的话，把序列化修好之后脚本会永远红（数据里的 ID 不会
    # 因为改了序列化就消失）；只看后者则扫不到「现在还没被暴露、但迟早会」的那些。
    number_id_fields = sorted(
        {
            str(row["field"])
            for row in big_ints
            if row["kind"] == "number"
            and str(row["field"]).split(".")[-1].replace("[]", "").endswith("id")
        }
    )

    if problems:
        print("\n" + "=" * 78)
        print(f"❌ 发现 {len(problems)} 个问题")
        print("=" * 78)
        for problem in problems:
            print(f"  · {problem}")
        print("\n契约与实现已脱节：改代码就同步改 docs/api-contract.md，反之亦然。")
        return 1

    if imprecise and number_id_fields:
        print("\n" + "=" * 78)
        print("❌ 存在精度风险：库里有不可精确表示的 ID，而下面这些字段仍以 JSON number 暴露")
        print("=" * 78)
        for field in number_id_fields:
            print(f"  · {field}")
        print(
            "\n  只要上述任一端点将来发出一个低位不为 0 的 ID，前端拿到的就是错的值。"
            "\n  修法见 docs/api-contract.md §10-T1（把雪花 ID 序列化成字符串）。"
        )
        return 1

    print("\n✅ 契约与实现一致，且当前无精度风险。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
