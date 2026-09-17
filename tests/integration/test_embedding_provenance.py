"""向量的来源标记（B3.1 §4.6）。

**它防的是最难发现的一类失效**：换 embedding 模型之后如果不重算存量向量，
检索会拿新模型去算旧向量的余弦 —— pgvector 照常返回一个**看起来完全正常的分数**，
没有任何地方会察觉两个向量根本不在同一个空间里。不报错、不告警、结果看着正常。

这个文件钉住三件事：
1. `ok` / `stale` / `unknown` / `empty` 四种状态的判定（**`unknown` 不等于 `ok`**）；
2. 「只统计**实际参与检索**的行」—— 否则一个健康系统上会永远报警，
   而永远在报警的检查最后会被人忽略；
3. 写入路径确实把来源记下来了。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from requirement_agent.infrastructure.db.session import SessionLocal
from requirement_agent.infrastructure.vector.embedding_provenance import (
    VectorProvenance,
    inspect_embedding_provenance,
    provenance_verdict,
)


def _report(**overrides) -> VectorProvenance:
    base = dict(
        table="t", total=3, by_model={"m1": 3}, unknown=0, current_model="m1"
    )
    base.update(overrides)
    return VectorProvenance(**base)


# ── ① 四种状态 ────────────────────────────────────────────────────────────


def test_all_from_current_model_is_ok() -> None:
    assert _report().status == "ok"


def test_foreign_model_is_stale() -> None:
    """存在**别的模型**算的向量 —— 最明确的不可信。"""
    assert _report(by_model={"m1": 2, "m0": 1}).status == "stale"


def test_unknown_is_not_ok() -> None:
    """**`unknown` 不能当成 `ok`。**

    存量行的 `embedding_model` 是 NULL，准确含义是「不知道」，不是「与当前模型一致」。
    把它们当作匹配，正好会在**换模型时**放过它们 —— 那时它们可能来自任何一个旧模型。
    """
    assert _report(by_model={}, unknown=3).status == "unknown"
    # 哪怕当前模型的向量一条都没有、别的模型也一条都没有，仍然不能算 ok
    assert _report(total=1, by_model={}, unknown=1).status == "unknown"


def test_empty_table_is_neither_ok_nor_stale() -> None:
    """空表既不是「可信」也不是「有问题」—— 它还没有向量，无从谈起。"""
    assert _report(total=0, by_model={}, unknown=0).status == "empty"
    # 空表不该让整体判定变红
    assert not any(r.status in ("stale", "unknown") for r in [_report(total=0, by_model={})])


def test_foreign_model_wins_over_unknown() -> None:
    """两者同时存在时报 `stale` —— 那是更明确、更需要立刻处理的信号。"""
    assert _report(by_model={"m1": 1, "m0": 1}, unknown=1).status == "stale"


# ── ② 真实库：口径与写入 ──────────────────────────────────────────────────


def test_real_database_provenance_is_trusted() -> None:
    """当前开发库里三张向量表都应当来自当前模型。

    这条会在**换了 embedding 模型却没重算**时变红 —— 那正是它该做的。
    修法是把输出里的 `action` 复制执行（`python -m scripts.reindex_embeddings`）。
    """
    trusted, reason = provenance_verdict()
    assert trusted, f"库里的向量来源不可信：{reason}"


def test_inspection_only_counts_rows_that_are_actually_used() -> None:
    """**只统计实际参与检索的行。**

    `memory_note` 里 `status='deleted'` 的行不参与召回，它们的向量是不是当前模型
    算的无所谓。不排除的话，一个健康系统上会永远报「来源未知」——
    而永远在报警的检查最后会被人忽略，那它就白做了。

    实测撞到过：不排除时 `memory_note` 恒为 `unknown`（4 条已删除的记忆）。
    """
    report = next(r for r in inspect_embedding_provenance() if r.table == "memory_note")
    with SessionLocal() as session:
        in_use = session.execute(
            text(
                "SELECT count(*) FROM memory_note "
                "WHERE embedding IS NOT NULL AND status <> 'deleted'"
            )
        ).scalar()
        with_vectors = session.execute(
            text("SELECT count(*) FROM memory_note WHERE embedding IS NOT NULL")
        ).scalar()

    assert report.total == int(in_use), "统计口径必须与召回一致"


def test_stored_vectors_carry_their_model() -> None:
    """写入路径确实把来源记下来了 —— 否则上面所有检出都是空中楼阁。"""
    from requirement_agent.infrastructure.embedding.embedding_service import (
        current_embedding_model,
    )

    current = current_embedding_model()
    with SessionLocal() as session:
        for table, in_use in (
            ("requirement_embedding", "TRUE"),
            ("document_chunk", "TRUE"),
            ("memory_note", "status <> 'deleted'"),
        ):
            unknown = session.execute(
                text(
                    f"SELECT count(*) FROM {table} "
                    f"WHERE embedding IS NOT NULL AND ({in_use}) AND embedding_model IS NULL"
                )
            ).scalar()
            assert int(unknown) == 0, (
                f"{table} 里有 {unknown} 条向量没有记录来源 —— "
                "跑 `python -m scripts.reindex_embeddings` 补上"
            )


# ── ③ 与 MODEL_ROUTES 一致 ────────────────────────────────────────────────


def test_current_embedding_model_follows_the_route_config() -> None:
    """记来源与**实际用的模型**必须是同一个 —— 记错比不记更坏。

    实现在 `current_embedding_model()`：统一走注册表，而不是直接读
    `settings.embedding_model`（那会忽略 `MODEL_ROUTES["embedding"]`）。
    """
    import json

    from requirement_agent.config.settings import settings
    from requirement_agent.infrastructure.embedding.embedding_service import (
        EmbeddingService,
        current_embedding_model,
    )

    original = settings.model_routes
    try:
        settings.model_routes = json.loads(
            '{"embedding": {"provider": "deepseek", "model": "routed-embedding-model"}}'
        )
        assert current_embedding_model() == "routed-embedding-model"
        # 实际发请求用的那个模型也必须是它
        assert EmbeddingService().model_name == "routed-embedding-model"
    finally:
        settings.model_routes = original


# ── ④ 顺手修掉的既有缺陷：分片指纹被重算抹掉（B3.1）──────────────────────


def test_chunk_content_hash_formula_matches_the_migration() -> None:
    """**公式必须与迁移 019 的回填逐字一致。**

    `content_hash` 是迁移 019 加的，公式
    `encode(sha256(convert_to(btrim(chunk_text), 'UTF8')), 'hex')`，
    但**它只在 019 里回填过一次，代码里从来没有人写** ——
    而 `add_chunks` 是「先 DELETE 再 INSERT」，于是任何一次重新分片
    （上传新版本、跑 `reindex_embeddings`）都会把已有分片的 hash 抹成 NULL，
    `(document_id, content_hash)` 那个去重索引随之失效。

    实测撞到过：跑一次重算脚本抹掉了 6 条分片的 hash，`test_document_version_chain`
    里那条 `chunks_without_hash == 0` 立刻变红。

    这条测试比对**两种实现**（Python 侧 vs Postgres 侧）的结果 ——
    公式一旦漂移，老行与新行会得到两种 hash，去重索引就形同虚设。
    """
    from requirement_agent.infrastructure.db.repositories.document import (
        _chunk_content_hash,
    )

    samples = ["  前后有空格的分片  ", "普通中文分片", "mixed 中英 text\n带换行"]
    with SessionLocal() as session:
        for sample in samples:
            expected = session.execute(
                text("SELECT encode(sha256(convert_to(btrim(:t), 'UTF8')), 'hex')"),
                {"t": sample},
            ).scalar()
            assert _chunk_content_hash(sample) == expected, f"公式漂移：{sample!r}"


def test_add_chunks_records_provenance_and_hash() -> None:
    """真跑一次 `add_chunks`：**来源与指纹都要写上**。

    这条守的是「写入路径必须带元数据」这一类缺陷 —— 本次批量里出现了两次：
    向量不记模型（§4.6）与分片不记 hash。两次都是「写的时候没带上，事后谁也补不回来」。

    造一条临时 `document_asset` 来跑，跑完连同分片一起删干净。
    """
    from requirement_agent.infrastructure.db.repositories import DocumentAssetRepository

    repo = DocumentAssetRepository()
    document_id: int | None = None
    try:
        with SessionLocal() as session:
            document_id = int(
                session.execute(
                    text(
                        """
                        INSERT INTO document_asset
                            (file_name, content_type, storage_uri, checksum, size_bytes,
                             metadata, status)
                        VALUES ('b31-provenance-probe.txt', 'text/plain', 'probe://x',
                                'probe-checksum', 0, '{}'::jsonb, 'current')
                        RETURNING id
                        """
                    )
                ).scalar()
            )
            session.commit()

        repo.add_chunks(document_id, "分片内容甲。分片内容乙。", chunk_size=600)
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    "SELECT chunk_text, content_hash, embedding_model, embedding_dimension "
                    "FROM document_chunk WHERE document_id = :d"
                ),
                {"d": document_id},
            ).fetchall()

        assert rows, "应当写出分片"
        for chunk_text, content_hash, model, dimension in rows:
            assert content_hash, (
                "分片必须带 content_hash —— 不带的话，任何一次重新分片都会抹掉"
                "既有的 hash，(document_id, content_hash) 去重索引随之失效"
            )
            assert len(str(content_hash)) == 64, "sha256 十六进制是 64 位"
            assert model, "分片向量必须记来源模型（§4.6）"
            assert dimension == 4096
    finally:
        if document_id is not None:
            with SessionLocal() as session:
                session.execute(
                    text("DELETE FROM document_chunk WHERE document_id = :d"), {"d": document_id}
                )
                session.execute(
                    text("DELETE FROM document_asset WHERE id = :d"), {"d": document_id}
                )
                session.commit()
