"""重建存量数据的 embedding（后台消费循环之外的一次性工具）。

用途：切换 embedding 模型/维度、或迁移清空向量后，为存量数据重新生成向量。
用法：
    python -m scripts.reindex_embeddings [--dry-run]
覆盖三张表：requirement_master（requirement_embedding）、document_asset（document_chunk）、memory_note。
未配置 embedding 网关时打印提示并退出。
"""

from __future__ import annotations

import sys

from sqlalchemy import text

from requirement_agent.infrastructure.db.repositories import DocumentAssetRepository
from requirement_agent.infrastructure.db.session import SessionLocal
from requirement_agent.infrastructure.embedding.embedding_service import EmbeddingService
from requirement_agent.infrastructure.vector.pgvector_repository import RequirementVectorRepository


def main(dry_run: bool = False) -> int:
    embed = EmbeddingService()
    vec_repo = RequirementVectorRepository()
    doc_repo = DocumentAssetRepository()

    if not embed.is_configured():
        print("[skip] embedding 网关未配置（EMBEDDING_BASE_URL / EMBEDDING_API_KEY），未重建向量。")
        return 1

    with SessionLocal() as s:
        masters = s.execute(
            text("SELECT id, requirement_key, final_requirement FROM requirement_master ORDER BY id")
        ).mappings().all()
        docs = s.execute(
            text("SELECT id, extracted_text, original_text FROM document_asset ORDER BY id")
        ).mappings().all()
        mems = s.execute(
            text("SELECT id, content FROM memory_note WHERE status IN ('active','superseded') ORDER BY id")
        ).mappings().all()

    print(f"masters={len(masters)} docs={len(docs)} memories={len(mems)}" + ("  [dry-run]" if dry_run else ""))
    if dry_run:
        return 0

    failures: list[tuple[str, str, str]] = []
    for m in masters:
        content = (m["final_requirement"] or "").strip()
        if not content:
            continue
        try:
            vec_repo.upsert(m["id"], embed.embed(content), source_text=content)
            print("  master", m["requirement_key"], "ok")
        except Exception as exc:  # noqa: BLE001
            failures.append(("master", str(m["requirement_key"]), str(exc)[:160]))
            print("  FAIL master", m["requirement_key"], str(exc)[:160])

    for d in docs:
        content = (d["extracted_text"] or d["original_text"] or "").strip()
        if not content:
            continue
        try:
            saved = doc_repo.add_chunks(d["id"], content, chunk_size=600, overlap=120)
            print("  doc", d["id"], "->", len(saved), "chunks")
        except Exception as exc:  # noqa: BLE001
            failures.append(("doc", str(d["id"]), str(exc)[:160]))
            print("  FAIL doc", d["id"], str(exc)[:160])

    for mem in mems:
        content = (mem["content"] or "").strip()
        if not content:
            continue
        try:
            vector = embed.embed(content)
            vec_str = "[" + ",".join(str(float(x)) for x in vector) + "]"
            with SessionLocal() as s:
                # 向量与**来源**必须一起写（B3.1 §4.6）：只改向量不改模型名，
                # 会让「这条向量是谁算的」说谎，比不记更坏。
                s.execute(
                    text(
                        "UPDATE memory_note SET embedding = CAST(:vec AS vector), "
                        "embedding_model = :model, embedding_dimension = :dim, "
                        "updated_at = NOW() WHERE id = :id"
                    ),
                    {
                        "vec": vec_str,
                        "id": mem["id"],
                        "model": embed.model_name,
                        "dim": len(vector),
                    },
                )
                s.commit()
            print("  memory", mem["id"], "ok")
        except Exception as exc:  # noqa: BLE001
            failures.append(("memory", str(mem["id"]), str(exc)[:160]))
            print("  FAIL memory", mem["id"], str(exc)[:160])

    print("\nfailures:", failures if failures else "none")

    # 重算完顺带报一次来源盘点 —— 这个脚本的用途就是「换模型后清库重算」，
    # 跑完到底清干净没有，是使用者最想知道的一件事。
    if not dry_run:
        from requirement_agent.infrastructure.vector.embedding_provenance import (
            inspect_embedding_provenance,
        )

        print("\n向量来源盘点：")
        for report in inspect_embedding_provenance():
            print(f"  {report.table:<22} {report.status:<8} 按模型 {report.by_model} 未知 {report.unknown}")
    return 0 if not failures else 2


if __name__ == "__main__":
    sys.exit(main(dry_run="--dry-run" in sys.argv))