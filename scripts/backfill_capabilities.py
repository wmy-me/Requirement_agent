"""为存量功能条目回填能力关联（一次性工具，方案批次 6）。

**为什么需要**：现有 feature 是在能力模型上线**之前**落库的，它们的抽取结果
（`requirement_source.metadata.extracted`）里没有 `capabilities[]`。所以这些需求
在能力视图里是空的 —— 不是没能力，是当时还没有这个概念。

**做法**：按**功能条目的正文**重新做一次能力抽取（不是重跑整套抽取），
与词表比对，按 `proposed` 建立关联。刻意不重跑完整抽取：那样既贵，
又可能覆盖既有的人工编辑结果。

**安全**：默认 `--dry-run`，只打印将要写入什么，一行不写。加 `--apply` 才落库。
写入一律 `proposed` —— 回填是 AI 提议，**正式成立仍需人工确认**（方案 §11）。

用法：
    python -m scripts.backfill_capabilities --dry-run          # 预演（默认）
    python -m scripts.backfill_capabilities --apply            # 落库
    python -m scripts.backfill_capabilities --apply --limit 2  # 只回填前 2 条需求
"""

from __future__ import annotations

import json
import re
import sys

from sqlalchemy import text

from requirement_agent.application.capability_match_service import CapabilityMatchService
from requirement_agent.infrastructure.db.repositories import (
    FeatureCapabilityRepository,
    RequirementFeatureRepository,
)
from requirement_agent.infrastructure.db.session import SessionLocal
from requirement_agent.infrastructure.llm.openai_provider import LLMProvider
from requirement_agent.skills.prompts import EXTRACT_SYSTEM_PROMPT, build_capability_backfill_prompt


def _parse_json(raw: str) -> dict:
    """剥掉可能的 ```json 围栏后解析。"""
    cleaned = re.sub(r"^```(?:json)?|```$", "", (raw or "").strip(), flags=re.M).strip()
    return json.loads(cleaned)


def _requirements_to_backfill(limit: int | None) -> list[dict]:
    """列出「有 active 功能但还没有任何能力关联」的需求主线。"""
    sql = """
        SELECT m.id, m.requirement_key, m.requirement_name
        FROM requirement_master m
        WHERE m.status = 'active'
          AND EXISTS (
              SELECT 1 FROM requirement_feature f
              WHERE f.requirement_id = m.id AND f.status = 'active'
          )
          AND NOT EXISTS (
              SELECT 1 FROM feature_capability fc
              JOIN requirement_feature f2 ON f2.id = fc.feature_id
              WHERE f2.requirement_id = m.id
          )
        ORDER BY m.requirement_key
    """
    if limit:
        sql += " LIMIT :limit"
    with SessionLocal() as session:
        rows = session.execute(text(sql), {"limit": limit} if limit else {}).mappings().all()
    return [dict(row) for row in rows]


def main(*, dry_run: bool = True, limit: int | None = None) -> int:
    provider = LLMProvider()
    if not provider.is_configured():
        print("[skip] LLM 网关未配置，无法回填能力。")
        return 1

    targets = _requirements_to_backfill(limit)
    if not targets:
        print("没有需要回填的需求（都有能力关联了，或没有 active 功能）。")
        return 0

    mode = "预演（不写库）" if dry_run else "落库"
    print(f"待回填 {len(targets)} 条需求 · 模式：{mode}\n")

    feature_repo = RequirementFeatureRepository()
    match_service = CapabilityMatchService()
    link_repo = FeatureCapabilityRepository()

    total_links = 0
    failures: list[str] = []

    for item in targets:
        key = item["requirement_key"]
        features = feature_repo.list_active(int(item["id"]))
        if not features:
            continue
        texts = [str(f.get("content") or "") for f in features]

        try:
            payload = _parse_json(
                provider.generate(
                    build_capability_backfill_prompt(feature_texts=texts),
                    system_prompt=EXTRACT_SYSTEM_PROMPT,
                )
            )
        except Exception as exc:
            failures.append(f"{key}: {type(exc).__name__} {str(exc)[:60]}")
            print(f"  {key}: ❌ 抽取失败（跳过）")
            continue

        # **靠 index 对回 feature，不靠文本匹配** —— 功能正文里可能有重复行
        by_index = {
            int(cap["index"]): cap
            for cap in payload.get("capabilities") or []
            if isinstance(cap, dict) and str(cap.get("index", "")).isdigit()
        }
        # 先把有效序号定下来：hits 与 candidates 一一对应，配对时不能再各自过滤，
        # 否则序号会错位（模型给出的 index 可能越界）
        indices = [index for index in sorted(by_index) if 1 <= index <= len(texts)]
        candidates = [
            {
                "raw_text": texts[index - 1],
                "action": by_index[index].get("action"),
                "object": by_index[index].get("object"),
                "constraints": by_index[index].get("constraints") or [],
            }
            for index in indices
        ]

        # 预演时 persist=False —— 连提案都不建，否则「--dry-run」是骗人的
        matched = match_service.match({"capabilities": candidates}, persist=not dry_run)
        hits = matched["capabilities"]
        links = [
            {
                "feature_id": int(features[index - 1]["id"]),
                "capability_id": int(hit["capability_id"]),
                "raw_text": texts[index - 1][:2000],
            }
            for index, hit in zip(indices, hits)
            if hit.get("capability_id")  # 预演时恒为 None，不建关联
        ]

        newly = len(hits)
        matched_n = matched["summary"]["capability_matched"]
        unmatched_n = matched["summary"]["capability_proposed"]
        cons = matched["constraints"]
        print(
            f"  {key}: 功能 {len(features)} 条 → 能力候选 {newly} 条"
            f"（命中词表 {matched_n} · 新提案 {unmatched_n}）"
            f" | 条件 命中 {len(cons['matched'])} / 未入表 {len(cons['unmatched'])}"
        )
        if dry_run:
            for link in links:
                print(f"      （预演）会挂 feature_id={link['feature_id']} → capability_id={link['capability_id']}")
        else:
            total_links += link_repo.link_many(links=links)

    print()
    if dry_run:
        print("预演结束，**未写任何数据**。确认无误后加 --apply 落库。")
    else:
        print(f"回填完成：新增关联 {total_links} 条（全部 review_status=proposed，待人工确认）。")
    if failures:
        print("失败：", "; ".join(failures))
        return 2
    return 0


def _limit_from_argv() -> int | None:
    """从 `--limit N` 取条数；不传或非法则返回 None（全量）。"""
    if "--limit" in sys.argv:
        position = sys.argv.index("--limit") + 1
        if position < len(sys.argv) and sys.argv[position].isdigit():
            return int(sys.argv[position])
    return None


if __name__ == "__main__":
    sys.exit(main(dry_run="--apply" not in sys.argv, limit=_limit_from_argv()))
