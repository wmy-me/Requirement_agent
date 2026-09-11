"""MemoryExtractor / MemoryContextBuilder 单元测试（fake repo/LLM/embedding，不连库）。"""

from src.requirement_agent.application.memory_service import MemoryContextBuilder, MemoryExtractor


class FakeEmbedding:
    def embed(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3, 0.4]


class FakeLLM:
    def __init__(self, text: str = "", raises: bool = False) -> None:
        self.text = text
        self.raises = raises

    def is_configured(self) -> bool:
        return True

    def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        if self.raises:
            raise RuntimeError("llm down")
        return self.text


class FakeMemoryRepo:
    """在内存中记录 insert/supersede；提供召回接口供 ContextBuilder 使用。"""

    def __init__(self, notes: list[dict] | None = None) -> None:
        self.notes = list(notes or [])
        self.superseded_calls: list[tuple[int, int]] = []
        self._seq = 100

    # —— 写入 ——
    def insert_memory(self, **kw) -> dict:
        self._seq += 1
        note = {"id": self._seq, "actor_id": kw.get("actor_id"), "kind": kw.get("kind"),
                "content": kw.get("content"), "status": "active", "active": True,
                "source_conversation_id": kw.get("source_conversation_id"),
                "source_message_id": kw.get("source_message_id"),
                "embedding": kw.get("embedding"), "importance": kw.get("importance", 1)}
        self.notes.append(note)
        return note

    def supersede(self, old_id: int, new_id: int):
        self.superseded_calls.append((old_id, new_id))
        for n in self.notes:
            if n["id"] == old_id:
                n["status"] = "superseded"
                n["active"] = False
        return {"id": old_id, "actor_id": "api-user", "status": "superseded"}

    # —— 召回 ——
    def recall(self, actor_id: str, query: str, limit: int = 4) -> list[dict]:
        return [n for n in self.notes if n.get("actor_id") == actor_id and n.get("status") == "active" and query in str(n.get("content") or "")][:limit]

    def recall_vector(self, actor_id: str, query_vector: list[float], limit: int = 4) -> list[dict]:
        return [n for n in self.notes if n.get("actor_id") == actor_id and n.get("status") == "active"][:limit]

    def list_memories(self, actor_id: str = "api-user", limit: int = 20) -> list[dict]:
        return [n for n in self.notes if n.get("actor_id") == actor_id][:limit]


def _extractor(repo, llm_text):
    return MemoryExtractor(memory_repo=repo, llm=FakeLLM(llm_text), embedding_service=FakeEmbedding())


def test_add_memory_from_llm() -> None:
    repo = FakeMemoryRepo()
    llm = '{"memories":[{"kind":"preference","content":"用户偏好按部门口径汇报","importance":2,"op":"add"}]}'
    result = MemoryExtractor(memory_repo=repo, llm=FakeLLM(llm), embedding_service=FakeEmbedding()).extract_from_conversation(
        actor_id="api-user", conversation_id="conv-1", messages=[{"id": 5, "role": "user", "content": "以后都按部门口径"}]
    )
    assert result == {"stored": 1, "superseded": 0, "skipped": 0}
    assert repo.notes[0]["kind"] == "preference"
    assert repo.notes[0]["embedding"] == [0.1, 0.2, 0.3, 0.4]
    assert repo.notes[0]["source_conversation_id"] == "conv-1"
    assert repo.notes[0]["source_message_id"] == 5


def test_supersede_marks_old_note() -> None:
    repo = FakeMemoryRepo([{"id": 1, "actor_id": "api-user", "kind": "decision", "content": "Embedding 用模型 A", "status": "active"}])
    llm = '{"memories":[{"kind":"decision","content":"Embedding 改用模型 B","importance":3,"op":"supersede","supersede_note_id":1}]}'
    result = _extractor(repo, llm).extract_from_conversation(
        actor_id="api-user", conversation_id="conv-9", messages=[{"id": 2, "role": "assistant", "content": "那就换模型 B"}], existing_notes=repo.notes
    )
    assert result["stored"] == 1 and result["superseded"] == 1
    assert repo.superseded_calls == [(1, 101)]
    assert repo.notes[0]["status"] == "superseded"


def test_skip_and_empty_content_not_stored() -> None:
    repo = FakeMemoryRepo()
    llm = '{"memories":[{"kind":"fact","content":"普通闲聊","op":"skip"},{"kind":"fact","content":"  ","op":"add"}]}'
    result = _extractor(repo, llm).extract_from_conversation(actor_id="api-user", conversation_id="c", messages=[{"id": 1, "role": "user", "content": "hi"}])
    assert result == {"stored": 0, "superseded": 0, "skipped": 2}


def test_llm_failure_is_silent() -> None:
    repo = FakeMemoryRepo()
    ex = MemoryExtractor(memory_repo=repo, llm=FakeLLM(raises=True), embedding_service=FakeEmbedding())
    result = ex.extract_from_conversation(actor_id="api-user", conversation_id="c", messages=[{"id": 1, "role": "user", "content": "x"}])
    assert result == {"stored": 0, "superseded": 0, "skipped": 0}
    assert repo.notes == []


def test_context_builder_empty_and_merge() -> None:
    empty = MemoryContextBuilder(memory_repo=FakeMemoryRepo(), embedding_service=FakeEmbedding())
    assert empty.build_context("api-user", "任何查询") == ""

    notes = [
        {"id": 1, "actor_id": "api-user", "kind": "decision", "content": "报表默认按部门维度", "status": "active"},
        {"id": 2, "actor_id": "api-user", "kind": "preference", "content": "导出用 Excel", "status": "active"},
    ]
    builder = MemoryContextBuilder(memory_repo=FakeMemoryRepo(notes), embedding_service=FakeEmbedding())
    ctx = builder.build_context("api-user", "部门", limit=4)
    assert "报表默认按部门维度" in ctx
    assert "长时记忆" in ctx
