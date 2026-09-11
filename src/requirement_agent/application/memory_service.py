"""长期记忆：召回上下文构建 + 会话记忆抽取（MemoryCandidate→LLM 准入→写入/替代）。"""

from __future__ import annotations

import json
from typing import Any, Callable

from requirement_agent.infrastructure.db.repositories import MemoryRepository
from requirement_agent.infrastructure.embedding.embedding_service import EmbeddingService
from requirement_agent.infrastructure.llm.openai_provider import LLMProvider


def _embed_safe(embedding_service: EmbeddingService, text: str) -> list[float] | None:
    try:
        vector = embedding_service.embed(text)
        if isinstance(vector, list) and vector:
            return [float(v) for v in vector]
    except Exception:
        return None
    return None


class MemoryContextBuilder:
    """为“新对话”构建 actor 作用域的记忆上下文（关键词 + 向量召回合并，按 id 去重）。"""

    def __init__(
        self,
        memory_repo: MemoryRepository | None = None,
        embedding_service: EmbeddingService | None = None,
    ) -> None:
        self.memory_repo = memory_repo or MemoryRepository()
        self.embedding_service = embedding_service or EmbeddingService()

    def build_context(self, actor_id: str, query: str, limit: int = 4) -> str:
        """无记忆返回空串（调用方据此不注入，避免提示漂移）。"""
        notes: list[dict[str, object]] = []
        seen: set[int] = set()

        try:
            for note in self.memory_repo.recall(actor_id, query, limit=limit):
                notes.append(note)
                seen.add(int(note["id"]))
        except Exception:
            pass

        # 仅当该 actor 已有记忆时才做向量召回，避免无意义的外部 embedding 调用
        try:
            has_memory = bool(self.memory_repo.list_memories(actor_id=actor_id, limit=1))
        except Exception:
            has_memory = False
        if has_memory:
            query_vector = _embed_safe(self.embedding_service, query)
            if query_vector is not None:
                try:
                    for note in self.memory_repo.recall_vector(actor_id, query_vector, limit=limit):
                        if int(note["id"]) not in seen:
                            notes.append(note)
                            seen.add(int(note["id"]))
                except Exception:
                    pass

        if not notes:
            return ""
        lines = []
        for note in notes[:limit]:
            lines.append(f"- [{note['kind']}] {note['content']}")
        return "长时记忆（供你参考，勿改动它）：\n" + "\n".join(lines)


class MemoryExtractor:
    """Memory Candidate → LLM 准入 → 写入（add）/ 替代（supersede）/ 跳过。

    遵循治理规则：只沉淀长期偏好、明确决策/被否方案、影响后续决策的事实等；
    普通闲聊、临时/一次性问题、测试数据、未经确认的推测不进入长期记忆。
    """

    SYSTEM_PROMPT = (
        "你是需求治理 Agent 的记忆沉淀器。用户与 Agent 的一段对话结束后，请判断是否有值得长期记住的内容。\n"
        "【值得记忆】用户明确的长期偏好、明确做出的架构/业务决策、明确被否决的方案、"
        "后续仍可能影响 Agent 决策的重要事实、用户明确要求记住的信息、与正式需求治理相关的决策/退回原因。\n"
        "【不要记】普通闲聊、临时问题、一次性调试/测试数据、无长期价值的问答、Agent 自己推测而用户未确认的内容。\n"
        "只返回 JSON：{\"memories\":[{\"kind\":\"preference|decision|fact|idea|rejected|followup|requirement_ref\","
        "\"content\":\"一句话记忆\",\"importance\":1,\"op\":\"add|skip|supersede\","
        "\"supersede_note_id\":<可选，仅当明确覆盖某条已有记忆时给其 id>}]}，不要输出 JSON 之外内容。"
    )

    def __init__(
        self,
        memory_repo: MemoryRepository | None = None,
        llm: Any | None = None,
        embedding_service: EmbeddingService | None = None,
    ) -> None:
        self.memory_repo = memory_repo or MemoryRepository()
        self.llm = llm or LLMProvider()
        self.embedding_service = embedding_service or EmbeddingService()

    def extract_from_conversation(
        self,
        *,
        actor_id: str,
        conversation_id: str,
        messages: list[dict[str, object]],
        existing_notes: list[dict[str, object]] | None = None,
    ) -> dict[str, int]:
        """返回 {stored, superseded, skipped}。LLM 失败/未配置一律返回空结果，不污染记忆。"""
        if not messages:
            return {"stored": 0, "superseded": 0, "skipped": 0}
        try:
            if hasattr(self.llm, "is_configured") and not self.llm.is_configured():
                return {"stored": 0, "superseded": 0, "skipped": 0}
            text = self.llm.generate(self._build_prompt(messages, existing_notes), system_prompt=self.SYSTEM_PROMPT)
        except Exception:
            return {"stored": 0, "superseded": 0, "skipped": 0}

        memories = self._parse(text)
        if not memories:
            return {"stored": 0, "superseded": 0, "skipped": 0}

        active_by_id = {int(n["id"]): n for n in (existing_notes or [])}
        source_message_id = None
        if messages:
            source_message_id = messages[-1].get("id")
            source_message_id = int(source_message_id) if source_message_id is not None else None

        stored = superseded = skipped = 0
        for item in memories:
            op = item.get("op", "skip")
            content = str(item.get("content") or "").strip()
            if not content:
                skipped += 1
                continue
            if op not in {"add", "supersede"}:
                skipped += 1
                continue
            kind = str(item.get("kind") or "fact")
            importance = max(1, min(5, int(item.get("importance") or 1)))
            vector = _embed_safe(self.embedding_service, content)
            note = self.memory_repo.insert_memory(
                actor_id=actor_id,
                kind=kind,
                content=content,
                source_conversation_id=conversation_id,
                source_message_id=source_message_id,
                importance=importance,
                meta={"op": op, "source": "memory_extractor"},
                embedding=vector,
            )
            stored += 1
            if op == "supersede":
                old_id = int(item.get("supersede_note_id") or 0)
                if old_id in active_by_id and str(active_by_id[old_id].get("actor_id") or "") == actor_id:
                    self.memory_repo.supersede(old_id, int(note["id"]))
                    superseded += 1
        return {"stored": stored, "superseded": superseded, "skipped": skipped}

    def _build_prompt(self, messages: list[dict[str, object]], existing_notes: list[dict[str, object]] | None) -> str:
        tail = messages[-8:]
        transcript = "\n".join(f"{m.get('role')}: {str(m.get('content') or '')[:400]}" for m in tail)
        existing = ""
        if existing_notes:
            existing = "已有长期记忆：\n" + "\n".join(
                f"- id={n.get('id')} [{n.get('kind')}] {str(n.get('content') or '')[:200]}" for n in existing_notes
            )
        return (
            "请根据下面的对话片段判断值得长期记住的内容。\n\n"
            f"{existing}\n\n对话片段：\n{transcript}"
        )

    @staticmethod
    def _parse(text: str) -> list[dict[str, Any]]:
        cleaned = (text or "").strip()
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end <= start:
            return []
        try:
            payload = json.loads(cleaned[start : end + 1])
        except Exception:
            return []
        items = payload.get("memories") if isinstance(payload, dict) else None
        return items if isinstance(items, list) else []
