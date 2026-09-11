"""健康检查工具。"""

from __future__ import annotations


def health_check() -> dict[str, str]:
    """服务健康检查。"""
    return {"status": "ok"}


__all__ = ["health_check"]
