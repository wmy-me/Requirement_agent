"""公共时间工具：统一数据库/业务的时间口径。

约定：
- 数据库统一以 **UTC** 存储（所有时间列 `TIMESTAMPTZ`，`NOW()` 在 UTC 会话下生成）。
- 对外展示（API 序列化）统一转成 `settings.display_timezone`（默认东八区）并带偏移，
  避免出现「库里是 UTC、前端看到少 8 小时」的口径不一致。
"""

from __future__ import annotations

from datetime import datetime, timezone

from requirement_agent.config.settings import settings


def utc_now() -> datetime:
    """当前 UTC 时间（带时区），作为所有「需要代码生成时间」场景的唯一入口。"""
    return datetime.now(timezone.utc)


def as_display_iso(value: datetime | str | None) -> str | None:
    """把 UTC 时间转成业务展示时区的 ISO 串（含本地偏移）。

    - 入参为带时区 datetime：直接转目标时区。
    - 入参为 naive datetime：按 UTC 解释后转目标时区（naive 一律视为 UTC）。
    - 入参为字符串：尝试解析（带偏移则保留其偏移，naive 视为 UTC）后转换。
    - None 或解析失败返回 None（保持与既有 `.isoformat()` 可空语义一致）。
    """
    if value is None or value == "":
        return None
    dt = _coerce(value)
    if dt is None:
        return None
    return _to_display(dt).isoformat()


def _coerce(value: datetime | str) -> datetime | None:
    """把 datetime / ISO 字符串统一为带时区的 datetime（naive 视为 UTC）。"""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _to_display(dt: datetime) -> datetime:
    """转换到业务展示时区。"""
    try:
        from zoneinfo import ZoneInfo

        return dt.astimezone(ZoneInfo(settings.display_timezone))
    except Exception:  # noqa: BLE001 - 时区库/配置异常时退化为 UTC
        return dt.astimezone(timezone.utc)


def parse_display_time(value: str) -> datetime:
    """把用户/前端传入的时间字符串解析为展示时区的 aware datetime。

    - 带偏移的 ISO 串（如 `2026-09-01T10:00:00+08:00`）：按其偏移转换到展示时区。
    - naive 串（如 `2026-09-01` / `2026-09-01T10:00:00`）：**视为展示时区的本地时间**，
      用于时间窗过滤时符合"用户按本地时间筛选"的直觉，避免与库内 UTC 比较偏移 8 小时。
    """
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        try:
            from zoneinfo import ZoneInfo

            return parsed.replace(tzinfo=ZoneInfo(settings.display_timezone))
        except Exception:  # noqa: BLE001
            return parsed.replace(tzinfo=timezone.utc)
    return _to_display(parsed)


def as_utc_iso(value: datetime) -> str:
    """把 aware datetime 序列化为 UTC ISO 串，供数据库 timestamptz 比较使用。"""
    return value.astimezone(timezone.utc).isoformat()


__all__ = ["as_display_iso", "as_utc_iso", "parse_display_time", "utc_now"]
