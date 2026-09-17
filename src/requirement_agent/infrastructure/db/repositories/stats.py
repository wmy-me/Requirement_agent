"""总览页的聚合统计（前端工作台 F5）。

## 为什么要在后端聚合

这些数**前端自己算不出来**：它需要全量扫描 `requirement_source` 并按状态、渠道、
业务域、风险等级分组。让前端拉 300 条自己算，等于把聚合的代价从数据库挪到
**每个人的浏览器**，而且 `limit` 一满就静默算错（少算了）。

## 它读的是什么

全部来自**已有表**，不新增任何结构：

| 指标 | 来源 |
|---|---|
| 各类状态计数（审核漏斗） | `requirement_source.processing_status` |
| 渠道占比 | `requirement_source.source_type` |
| 业务域分布 | `requirement_source.metadata->>'business_domain'` |
| 风险矩阵 | `requirement_source.metadata->'risk'` |
| 冲突数 | `requirement_source.metadata->'analysis'->>'conflict'` |
| 需求总数 | `requirement_master.status = 'active'` |
| 死信数 | `outbox_event` |

⚠️ **风险/冲突是从 `metadata` 里读的**，而 metadata 是分析产物。没分析过的来源
（`received` / `extracting`）**不进这两个统计** —— 这是对的：它们还没有风险可言。
界面上要如实说明「基于已分析的 N 条」，而不是让人以为那是全量。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from requirement_agent.infrastructure.db.session import SessionLocal

__all__ = ["StatsRepository"]

#: 风险等级的三个维度（与 `application/decision_rules.RISK_KEYS` 同源）。
_RISK_KEYS = ("quality_risk", "change_risk", "technical_impact_risk")


class StatsRepository:
    """总览页的只读聚合。**不做任何写操作。**"""

    def overview(self, *, trend_periods: int = 12) -> dict[str, object]:
        """一次拿全总览页要的数。

        分多条查询而不是一条大 SQL：每条的聚合口径不同（有的按状态、有的按 JSONB
        字段、有的跨表），拼成一条会让「哪个数算错了」变得极难定位 ——
        而这些查询都是全表扫描级别，合并省下的那点往返不值得。
        """
        with SessionLocal() as session:
            status_counts = self._counts(
                session, "SELECT processing_status AS k, count(*) FROM requirement_source GROUP BY 1"
            )
            channel_counts = self._counts(
                session, "SELECT source_type AS k, count(*) FROM requirement_source GROUP BY 1"
            )
            domain_counts = self._counts(
                session,
                "SELECT COALESCE(metadata->>'business_domain', '未标注') AS k, count(*) "
                "FROM requirement_source GROUP BY 1",
            )
            risk_matrix = [
                {
                    "quality_risk": row[0],
                    "change_risk": row[1],
                    "count": int(row[2]),
                }
                for row in session.execute(
                    text(
                        """
                        SELECT COALESCE(metadata->'risk'->>'quality_risk', '未评估') AS q,
                               COALESCE(metadata->'risk'->>'change_risk', '未评估') AS c,
                               count(*)
                        FROM requirement_source
                        WHERE metadata ? 'risk'
                        GROUP BY 1, 2
                        ORDER BY 3 DESC
                        """
                    )
                ).fetchall()
            ]
            conflict_count = int(
                session.execute(
                    text(
                        "SELECT count(*) FROM requirement_source "
                        "WHERE metadata->'analysis'->>'conflict' = 'true'"
                    )
                ).scalar()
                or 0
            )
            high_risk_count = int(
                session.execute(
                    text(
                        "SELECT count(*) FROM requirement_source WHERE "
                        + " OR ".join(
                            f"metadata->'risk'->>'{key}' = 'high'" for key in _RISK_KEYS
                        )
                    )
                ).scalar()
                or 0
            )
            analysed_count = int(
                session.execute(
                    text("SELECT count(*) FROM requirement_source WHERE metadata ? 'risk'")
                ).scalar()
                or 0
            )
            requirements_total = int(
                session.execute(
                    text("SELECT count(*) FROM requirement_master WHERE status = 'active'")
                ).scalar()
                or 0
            )
            trend = [
                {"period": str(row[0]), "count": int(row[1])}
                for row in session.execute(
                    text(
                        """
                        SELECT to_char(date_trunc('week', submitted_at), 'IYYY-"W"IW') AS period,
                               count(*)
                        FROM requirement_source
                        GROUP BY 1
                        ORDER BY 1 DESC
                        LIMIT :n
                        """
                    ),
                    {"n": max(1, min(trend_periods, 52))},
                ).fetchall()
            ]
            dead_letter = int(
                session.execute(
                    text("SELECT count(*) FROM outbox_event WHERE status = 'dead_letter'")
                ).scalar()
                or 0
            )

        return {
            # 待办口径：待审 + 死信 + 高危 —— 这三样是「需要人动手」的
            "pending_review": status_counts.get("pending_review", 0),
            "pending_total": status_counts.get("pending_review", 0) + dead_letter,
            "high_risk": high_risk_count,
            "conflict": conflict_count,
            "dead_letter": dead_letter,
            "requirements_total": requirements_total,
            "sources_total": sum(status_counts.values()),
            # ⚠️ 风险与冲突只统计**分析过的**来源；如实给分母，免得被读成全量
            "analysed_sources": analysed_count,
            "source_status_counts": status_counts,
            "channel_counts": channel_counts,
            "domain_counts": domain_counts,
            "risk_matrix": risk_matrix,
            "submission_trend": list(reversed(trend)),  # 时间正序，便于直接画折线
        }

    @staticmethod
    def _counts(session, sql: str) -> dict[str, int]:
        """跑一条「键 + 计数」的聚合，键为空时归一到 `未标注`。

        JSONB 取出来的 `null` 与真正的空串都归到一档 —— 否则界面上会出现
        两行看起来一样的「空」。
        """
        out: dict[str, int] = {}
        for key, count in session.execute(text(sql)).fetchall():
            label = str(key).strip() if key is not None else ""
            out[label or "未标注"] = int(count)
        return out
