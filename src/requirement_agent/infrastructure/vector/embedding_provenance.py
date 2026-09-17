"""库里的向量是**哪个模型**算的（B3.1，追加实施文档 §4.6）。

## 它防的是什么

换 embedding 模型之后，如果旧向量不重算：

    · 库里是**旧模型**的向量，查询用**新模型**编码；
    · pgvector 照常算余弦，**返回一个看起来完全正常的分数**；
    · 没有任何地方会察觉两个向量根本不在同一个空间里。

不报错、不告警、结果看起来正常 —— 这是最难发现的一类失效。§4.6 的原话是
「模型变化后**不能**直接把新旧向量混在同一索引中」，在 B3.1 之前，
混不混完全取决于运维有没有记得手动清库。

## 三种状态，处置不同

| 状态 | 含义 | 该怎么办 |
|---|---|---|
| `ok` | 全部向量都来自当前模型 | 什么都不用做 |
| `stale` | 存在**别的模型**算的向量 | 跑 `scripts/reindex_embeddings.py` 重算 |
| `unknown` | 存在**来源未知**的存量向量（迁移 023 之前的行） | 同上 —— 无从知道是不是当前模型的 |

⚠️ **`unknown` 不能当成 `ok`。** 存量行的 `embedding_model` 是 NULL，
它的准确含义是「不知道」，而不是「与当前模型一致」。把它们当作匹配，
正好会在**换模型时**放过它们（那时它们可能来自任何一个旧模型）。

## 它不做什么

**不自动重算。** 重算是要花钱、要时间、要停机窗口的运维动作，
不该由一个查询路径偷偷触发。这里只负责**如实报出来**。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text

from requirement_agent.infrastructure.db.session import SessionLocal

__all__ = [
    "VectorProvenance",
    "inspect_embedding_provenance",
    "provenance_verdict",
]

#: 带向量的三张表 + **它们各自「哪些行真的参与检索」的谓词**。
#
# ⚠️ 谓词不是可有可无的：`memory_note` 里 `status='deleted'` 的行**不参与召回**，
# 它们的向量是不是当前模型算的无所谓。不排除的话，一个健康系统上会永远报
# 「来源未知」—— 而**永远在报警的检查最后会被人忽略**，那它就白做了。
#
# 这三条谓词要与各自的召回路径一致：
#   · requirement_embedding / document_chunk —— 全部行都进索引
#   · memory_note —— 召回只读非 deleted 的（`scripts/reindex_embeddings.py`
#     重算时用的也是这个口径）
_VECTOR_TABLES = (
    ("requirement_embedding", "TRUE"),
    ("document_chunk", "TRUE"),
    ("memory_note", "status <> 'deleted'"),
)

ProvenanceStatus = Literal["ok", "stale", "unknown", "empty"]


@dataclass(frozen=True, slots=True)
class VectorProvenance:
    """一张向量表的来源盘点。"""

    table: str
    total: int
    """该表里**有向量**的行数。"""
    by_model: dict[str, int]
    """模型名 → 行数。来源未知的行（NULL）不在这里。"""
    unknown: int
    """来源未知的行数（迁移 023 之前的存量）。"""
    current_model: str

    @property
    def status(self) -> ProvenanceStatus:
        if self.total == 0:
            return "empty"
        foreign = sum(count for model, count in self.by_model.items() if model != self.current_model)
        if foreign:
            return "stale"
        if self.unknown:
            # 来源未知**不等于**匹配 —— 见模块 docstring。哪怕别的模型一个都没有，
            # 这些行也可能是任意旧模型算的。
            return "unknown"
        return "ok"


def inspect_embedding_provenance() -> list[VectorProvenance]:
    """逐表盘点。**只读**，不做任何修改。"""
    from requirement_agent.infrastructure.embedding.embedding_service import (
        current_embedding_model,
    )

    current = current_embedding_model()
    results: list[VectorProvenance] = []
    with SessionLocal() as session:
        for table, in_use in _VECTOR_TABLES:
            rows = session.execute(
                text(
                    f"""
                    SELECT COALESCE(embedding_model, '<unknown>') AS model, count(*) AS n
                    FROM {table}
                    WHERE embedding IS NOT NULL AND ({in_use})
                    GROUP BY 1
                    """
                )
            ).fetchall()
            by_model = {str(r[0]): int(r[1]) for r in rows if r[0] != "<unknown>"}
            unknown = next((int(r[1]) for r in rows if r[0] == "<unknown>"), 0)
            results.append(
                VectorProvenance(
                    table=table,
                    total=sum(by_model.values()) + unknown,
                    by_model=by_model,
                    unknown=unknown,
                    current_model=current,
                )
            )
    return results


def provenance_verdict() -> tuple[bool, str]:
    """汇总成一个 `(是否可信, 中文说明)`。

    `False` 表示「至少有一张表里的向量不全是当前模型算的」—— 此时**检索出来的
    相似度不可信**，调用方应当把它当作一个真实的告警，而不是当成噪声。
    """
    reports = inspect_embedding_provenance()
    problems = [report for report in reports if report.status in ("stale", "unknown")]
    if not problems:
        return True, ""

    parts: list[str] = []
    for report in problems:
        if report.status == "stale":
            foreign = {
                model: count
                for model, count in report.by_model.items()
                if model != report.current_model
            }
            parts.append(f"{report.table}: {foreign}（当前模型 {report.current_model}）")
        else:
            parts.append(
                f"{report.table}: {report.unknown} 条来源未知"
                f"（迁移 023 之前的存量，无法判断是否为 {report.current_model}）"
            )
    return False, (
        "库中存在**不是当前 embedding 模型**算的向量 —— 检索出来的相似度不可信。"
        "跑 `python -m scripts.reindex_embeddings` 重算后再用。详情：" + "；".join(parts)
    )
