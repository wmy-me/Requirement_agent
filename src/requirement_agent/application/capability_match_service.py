"""把抽取产出的能力/条件候选与受控词表比对（方案批次 2）。

**这个服务是职责边界（方案 §11.8）的落点**，两条硬约束都体现在这里：

1. **不得创建正式能力**。未匹配的能力只写 `pending_confirmation` —— 该状态下
   `CapabilityRepository.find_exact` 根本不会命中它，所以即使模型抽错了，
   也不会污染后续任何一条匹配。
2. **不得自动创建条件**。未匹配的条件**一条都不写词表**，只作为候选返回。

第 2 条是**实测倒逼出来的**，不是保守起见：拿库里真实语料跑下来，
短的结构化需求条件抽取零噪声（5/5 准），但 2750 字的 PDF 版式文本上
模型会把「最多12个汉字」「同时在线1000人」「每日0点」这类**非功能约束**
当成限定条件，噪声率约 90%。若把它们全部写成 `pending_confirmation`，
一个需求就能产生 20+ 条垃圾提案，人工审核队列会被淹没。

条件身份的判定权交回人工（规范给的四个选项：合并已有条件 / 新增正式条件 /
作为别名 / 不结构化只留原文）。
"""

from __future__ import annotations

from typing import Any

from requirement_agent.infrastructure.db.repositories.capability import (
    ACTIVE,
    PENDING_CONFIRMATION,
    CapabilityRepository,
    ConstraintVocabRepository,
)

__all__ = ["CapabilityMatchService"]


def _text(value: Any) -> str:
    return str(value or "").strip()


class CapabilityMatchService:
    """候选 ↔ 词表的比对器。只读词表 + 只写提案，不改任何正式数据。"""

    def __init__(
        self,
        capability_repo: CapabilityRepository | None = None,
        constraint_repo: ConstraintVocabRepository | None = None,
    ) -> None:
        self.capability_repo = capability_repo or CapabilityRepository()
        self.constraint_repo = constraint_repo or ConstraintVocabRepository()

    def match(
        self,
        extracted: Any,
        *,
        source_id: int | None = None,
        persist: bool = True,
        session: Any = None,
    ) -> dict[str, object]:
        """比对一条抽取结果，返回匹配与提案清单。**不抛异常**。

        `extracted` 可以是 `ExtractedRequirement`，也可以是它的 `model_dump()` 结果 ——
        分析链路里两种形态都会遇到。

        `source_id` 会记在新提案的 `origin_source_id` 上：既让审核页能显示
        「这条能力是哪个需求提的」，也让来源删除时提案跟着级联清掉。
        **务必传**，否则测试清理会漏掉提案（实测踩过一次）。

        `persist=False` 时**一行都不写**（连提案也不建），只回报「会提议什么」。
        回填工具（`scripts/backfill_capabilities.py`）的预演靠它 ——
        预演必须真的只读，否则「--dry-run」就是骗人的：实测第一次跑就悄悄建了
        11 条提案，而得等到落库时才会有人发现。

        返回结构（可直接塞进 `requirement_source.metadata`）：

        ```
        {
          "business_object": "员工数据",
          "capabilities": [
            {"raw_text":…, "action":…, "object":…, "matched": true,
             "capability_id": 1, "display_name": "导出 Excel"},
            {"raw_text":…, "action":…, "object":…, "matched": false,
             "capability_id": 7, "status": "pending_confirmation"}
          ],
          "constraints": {
            "matched":   [{"raw": "按部门维度筛选", "constraint_key": "按部门筛选", "alias_hit": true}],
            "unmatched": [{"raw": "按区域层级导出"}]
          },
          "summary": {"capability_total": 2, "capability_matched": 1, "constraint_unmatched": 1}
        }
        ```
        """
        payload = self._as_dict(extracted)
        candidates = self._capability_candidates(payload)

        capability_hits: list[dict[str, object]] = []
        constraint_matched: list[dict[str, object]] = []
        constraint_unmatched: list[dict[str, object]] = []
        seen_capability: set[tuple[str, str]] = set()
        seen_constraint: set[str] = set()

        for candidate in candidates:
            action, object_ = _text(candidate.get("action")), _text(candidate.get("object"))
            if not action or not object_:
                continue
            key = (action, object_)
            if key in seen_capability:
                # 同一份抽取里重复提议同一条能力：只处理一次，避免重复写提案
                continue
            seen_capability.add(key)

            try:
                capability_hits.append(
                    self._match_capability(
                        candidate, action, object_, source_id=source_id,
                        persist=persist, session=session,
                    )
                )
            except Exception:
                # 词表比对失败不该拖垮整条分析链路（与抽取的降级策略一致）
                capability_hits.append(
                    {
                        "raw_text": _text(candidate.get("raw_text")),
                        "action": action,
                        "object": object_,
                        "matched": False,
                        "error": "match_failed",
                    }
                )

            for raw_constraint in candidate.get("constraints") or []:
                raw = _text(raw_constraint)
                if not raw or raw in seen_constraint:
                    continue
                seen_constraint.add(raw)
                self._match_constraint(
                    raw, constraint_matched, constraint_unmatched, session=session
                )

        return {
            "business_object": _text(payload.get("business_object")),
            "capabilities": capability_hits,
            "constraints": {"matched": constraint_matched, "unmatched": constraint_unmatched},
            "summary": {
                "capability_total": len(capability_hits),
                "capability_matched": sum(1 for item in capability_hits if item.get("matched")),
                "capability_proposed": sum(1 for item in capability_hits if item.get("proposed")),
                "constraint_matched": len(constraint_matched),
                "constraint_unmatched": len(constraint_unmatched),
            },
        }

    # ── 内部 ────────────────────────────────────────────────────────────

    def _match_capability(
        self,
        candidate: dict[str, Any],
        action: str,
        object_: str,
        *,
        source_id: int | None,
        persist: bool,
        session: Any,
    ) -> dict[str, object]:
        """命中 active 词表就直接引用；否则**只写 pending_confirmation 提案**。"""
        existing = self.capability_repo.find_exact(action, object_, session=session)
        if existing is not None:
            return {
                "raw_text": _text(candidate.get("raw_text")),
                "action": action,
                "object": object_,
                "matched": True,
                "capability_id": existing["id"],
                "display_name": existing["display_name"],
            }

        if not persist:
            # 预演：如实回报「会提议这条」，但不建行
            return {
                "raw_text": _text(candidate.get("raw_text")),
                "action": action,
                "object": object_,
                "matched": False,
                "proposed": True,
                "capability_id": None,
                "status": PENDING_CONFIRMATION,
            }
        proposal = self.capability_repo.create(
            action=action,
            object_=object_,
            status=PENDING_CONFIRMATION,  # ← 不是 ACTIVE：提案不参与匹配
            created_by="analysis",
            origin_source_id=source_id,
            session=session,
        )
        return {
            "raw_text": _text(candidate.get("raw_text")),
            "action": action,
            "object": object_,
            "matched": False,
            "proposed": proposal["status"] == PENDING_CONFIRMATION,
            "capability_id": proposal["id"],
            "status": proposal["status"],
        }

    def _match_constraint(
        self,
        raw: str,
        matched: list[dict[str, object]],
        unmatched: list[dict[str, object]],
        *,
        session: Any,
    ) -> None:
        """条件只做匹配，**未命中不入词表**（理由见模块 docstring）。"""
        existing = self.constraint_repo.find_exact(raw, session=session)
        if existing is not None:
            matched.append(
                {
                    "raw": raw,
                    "constraint_id": existing["id"],
                    "constraint_key": existing["constraint_key"],
                    # 命中的是别名还是正式键 —— 审核页据此提示「归到了哪个正式条件」
                    "alias_hit": raw != existing["constraint_key"],
                }
            )
            return
        unmatched.append({"raw": raw})

    @staticmethod
    def _as_dict(extracted: Any) -> dict[str, Any]:
        if isinstance(extracted, dict):
            return extracted
        dump = getattr(extracted, "model_dump", None)
        if callable(dump):
            return dump()
        return {}

    @staticmethod
    def _capability_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
        raw = payload.get("capabilities")
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, (list, tuple)):
            return []
        return [item for item in raw if isinstance(item, dict)]
