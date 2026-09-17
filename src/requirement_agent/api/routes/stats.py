"""总览页的聚合统计 HTTP 路由（只读）。

**为什么单独一层。** 这些数需要全表扫描并按状态/渠道/业务域/风险分组，
前端拉不全也算不对（`limit` 一满就静默少算）。收在后端一次算完。

口径说明见 `infrastructure/db/repositories/stats.py` —— 尤其是
「风险与冲突只统计**分析过的**来源」，响应里带了 `analysed_sources` 作分母。
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from requirement_agent.api.dependencies import stats_repo

router = APIRouter()


@router.get("/api/v1/stats/overview")
async def get_overview(
    trend_periods: int = Query(default=12, ge=1, le=52, description="趋势返回多少个周期（按周）"),
) -> dict[str, object]:
    """总览页的全部指标，一次拿全。

    返回里既有**标量**（`pending_review` / `high_risk` / `conflict` / `dead_letter`
    / `requirements_total` / `sources_total`），也有**分组**（`source_status_counts`
    审核漏斗、`channel_counts`、`domain_counts`、`risk_matrix`、`submission_trend`）。
    """
    return stats_repo.overview(trend_periods=trend_periods)
