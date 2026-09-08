from io import BytesIO

from docx import Document
from PIL import Image

from src.infrastructure.parser.document_parser import DocumentParser


def test_document_parser_cleans_segments_and_normalizes_fields() -> None:
    parser = DocumentParser()

    parsed = parser.parse(
        "login-requirement.txt",
        "\ufeff需求标题：短信登录\n第 1 页\n需求标题：短信登录\n\n业务域：用户中心\r\n\r\n用户登录需要支持短信验证码。\x00".encode(),
    )

    assert parsed.raw_content.startswith("\ufeff需求标题：短信登录")
    assert parsed.content == "需求标题：短信登录\n\n业务域：用户中心\n\n用户登录需要支持短信验证码。"
    assert parsed.normalized_fields == {"title": "短信登录", "business_domain": "用户中心"}
    assert [segment.kind for segment in parsed.segments or []] == ["field", "field", "paragraph"]


def test_document_parser_extracts_docx_and_image_metadata() -> None:
    parser = DocumentParser()

    doc = Document()
    doc.add_paragraph("需求标题：季度回款")
    doc.add_paragraph("业务域：财务管理")
    doc.add_paragraph("针对季度回款流程，需要支持自动催收提醒。")
    docx_buffer = BytesIO()
    doc.save(docx_buffer)
    parsed_docx = parser.parse("quarterly-payment.docx", docx_buffer.getvalue())
    assert "季度回款" in parsed_docx.content
    assert parsed_docx.normalized_fields["title"] == "季度回款"

    image = Image.new("RGB", (120, 80), color="white")
    image_buffer = BytesIO()
    image.save(image_buffer, format="PNG")
    parsed_image = parser.parse("invoice.png", image_buffer.getvalue())
    assert "图片附件" in parsed_image.content
    assert "尺寸=" in parsed_image.content
