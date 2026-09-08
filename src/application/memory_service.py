from __future__ import annotations

from src.infrastructure.db.repositories import MemoryRepository


class MemoryContextBuilder:
    """Build a compact, actor-scoped memory context for new chats."""

    def __init__(self, memory_repo: MemoryRepository | None = None) -> None:
        self.memory_repo = memory_repo or MemoryRepository()

    def build_context(self, actor_id: str, query: str, limit: int = 4) -> str:
        notes = self.memory_repo.recall(actor_id, query, limit=limit)
        if not notes:
            return "暂无跨会话长期记忆。"
        lines = []
        for note in notes:
            lines.append(f"- [{note['kind']}] {note['content']}")
        return "长时记忆：\n" + "\n".join(lines)
