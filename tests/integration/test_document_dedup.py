"""文档资产按内容去重的集成测试（需要真实 PostgreSQL）。

背景：对象存储的 object key 只由**文件名**生成，同名即覆盖。资产表若不去重，
就会出现「表里 N 条、存储里 1 个对象」的错位，甚至静默的数据损坏。

测试自带清理，跑完不留数据。
"""

import uuid

from sqlalchemy import text

from requirement_agent.common.snowflake import new_id
from requirement_agent.infrastructure.db.repositories.document import DocumentAssetRepository
from requirement_agent.infrastructure.db.session import SessionLocal


def _purge(checksum: str) -> None:
    with SessionLocal() as session:
        session.execute(
            text(
                "DELETE FROM document_chunk WHERE document_id IN "
                "(SELECT id FROM document_asset WHERE checksum = :c)"
            ),
            {"c": checksum},
        )
        session.execute(text("DELETE FROM document_asset WHERE checksum = :c"), {"c": checksum})
        session.commit()


def _save(repo: DocumentAssetRepository, *, checksum: str, file_name: str, uri: str):
    return repo.save(
        file_name=file_name,
        content_type="text/plain",
        storage_uri=uri,
        checksum=checksum,
        size_bytes=3,
        source_type="web",
        original_text="abc",
        extracted_text="abc",
    )


def test_same_checksum_is_deduplicated() -> None:
    """同一份内容（checksum 相同）第二次保存必须返回同一条，不插新行。"""
    repo = DocumentAssetRepository()
    checksum = f"test-{uuid.uuid4().hex}"
    try:
        first = _save(repo, checksum=checksum, file_name="原始文件.txt", uri="file:///tmp/one.txt")
        second = _save(repo, checksum=checksum, file_name="改了名的同内容文件.txt", uri="file:///tmp/two.txt")

        assert second["id"] == first["id"]
        assert second["file_name"] == "原始文件.txt"  # 返回既有那条，不被后来的覆盖

        with SessionLocal() as session:
            count = session.execute(
                text("SELECT count(*) FROM document_asset WHERE checksum = :c"), {"c": checksum}
            ).scalar()
        assert count == 1
    finally:
        _purge(checksum)


def test_different_checksum_creates_new_asset() -> None:
    """内容不同（checksum 不同）则是两份资产——去重只按内容，不按文件名。"""
    repo = DocumentAssetRepository()
    a, b = f"test-{uuid.uuid4().hex}", f"test-{uuid.uuid4().hex}"
    try:
        first = _save(repo, checksum=a, file_name="同名.txt", uri="file:///tmp/a.txt")
        second = _save(repo, checksum=b, file_name="同名.txt", uri="file:///tmp/b.txt")

        assert first["id"] != second["id"]
    finally:
        _purge(a)
        _purge(b)


def test_changed_content_advances_the_same_document_stream() -> None:
    """同名同格式的不同内容必须形成版本链，而非两条互不关联的资产。"""
    repo = DocumentAssetRepository()
    checksum_a, checksum_b = f"test-{uuid.uuid4().hex}", f"test-{uuid.uuid4().hex}"
    name = f"版本-{uuid.uuid4().hex}.txt"
    try:
        first = _save(repo, checksum=checksum_a, file_name=name, uri="file:///tmp/v1.txt")
        second = _save(repo, checksum=checksum_b, file_name=name, uri="file:///tmp/v2.txt")

        assert first["stream_id"] == second["stream_id"]
        assert first["version_no"] == 1 and first["status"] == "current"
        assert second["version_no"] == 2 and second["status"] == "current"
        versions = repo.list_versions(int(second["id"]))
        assert versions is not None
        assert [(item["version_no"], item["status"]) for item in versions] == [
            (2, "current"), (1, "superseded"),
        ]
        assert versions[1]["superseded_by_version_no"] == 2
    finally:
        _purge(checksum_a)
        _purge(checksum_b)
        with SessionLocal() as session:
            session.execute(
                text("DELETE FROM document_stream WHERE file_name = :name AND content_type = 'text/plain'"),
                {"name": name},
            )
            session.commit()


def test_find_by_checksum_handles_blank() -> None:
    repo = DocumentAssetRepository()

    assert repo.find_by_checksum("") is None
    assert repo.has_chunks(0) is False
    assert new_id() > 0  # 顺带确认雪花可用（本测试用到 new_id 的导入）
