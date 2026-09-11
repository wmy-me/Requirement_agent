"""Document parser for requirement files and attachments."""

from __future__ import annotations

import io
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover - optional dependency, handled gracefully
    PdfReader = None

try:
    from docx import Document as DocxDocument
except ImportError:  # pragma: no cover - optional dependency, handled gracefully
    DocxDocument = None

try:
    from PIL import Image
except ImportError:  # pragma: no cover - optional dependency, handled gracefully
    Image = None


@dataclass(slots=True)
class DocumentSegment:
    """解析结果的一个分段。

    - `kind`：段落类型（如正文/字段 title 行等），供下游结构化清洗使用。
    - `field_name`：若该段是「键：值」形式，提取出的字段名；否则为 None。
    """

    index: int
    kind: str
    text: str
    field_name: str | None = None


@dataclass(slots=True)
class ParsedDocument:
    """单个文档的解析产物，贯穿入库/切片/回放。

    - `content` / `raw_content`：清洗后文本 / 原始提取文本。
    - `segments`：分段结果；`normalized_fields`：从分段中规整出的字段表（如标题/申请人/优先级）。
    """

    title: str
    content: str
    raw_content: str
    extension: str
    pages: int = 1
    segments: list[DocumentSegment] | None = None
    normalized_fields: dict[str, str] | None = None


class DocumentParser:
    """解析上传的需求文档（pdf/docx/图片/纯文本）为规整文本 + 分段 + 规整字段。

    策略：按扩展名分发到具体抽取器；解析失败或结果为空时，退化为按文件名生成占位文案，
    保证「先上线、后续再补解析能力」的可用性。
    """

    def parse(self, file_name: str, payload: bytes) -> ParsedDocument:
        """解析上传文档为规整文本 + 分段 + 规整字段。

        按扩展名分发（pdf/docx/txt 等）；解析失败或空内容时退化为按文件名兜底。
        """
        extension = Path(file_name).suffix.lower() or ".txt"
        raw_text = self._extract_text(file_name, payload, extension)
        if not raw_text.strip():
            raw_text = self._fallback_from_name(file_name)

        text = self.clean_text(raw_text)
        segments = self.segment(text)
        normalized_fields = self.normalize_fields(segments)
        title = Path(file_name).stem or "requirement-document"
        pages = max(1, len(raw_text.splitlines()) // 25 + 1)
        return ParsedDocument(
            title=title,
            content=text,
            raw_content=raw_text,
            extension=extension,
            pages=pages,
            segments=segments,
            normalized_fields=normalized_fields,
        )

    def _extract_text(self, file_name: str, payload: bytes, extension: str) -> str:
        """按扩展名分发到具体抽取器；未知/文本型走 UTF-8 解码兜底。"""
        if extension == ".pdf":
            return self._extract_pdf_text(file_name, payload)
        if extension == ".docx":
            return self._extract_docx_text(file_name, payload)
        if extension in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}:
            return self._extract_image_text(file_name, payload)
        if extension in {".doc"}:
            return f"[文件附件：{file_name}] 当前不支持老版 .doc 直接抽取，已保留原文件并等待二次处理。"

        try:
            decoded = payload.decode("utf-8").strip()
        except UnicodeDecodeError:
            decoded = payload.decode("utf-8", errors="ignore").strip()
        if decoded:
            return decoded
        return self._fallback_from_name(file_name)

    def _extract_pdf_text(self, file_name: str, payload: bytes) -> str:
        """抽取 PDF 文本：逐页 `extract_text`，失败或缺库时返回可读占位文案而非抛错。"""
        if PdfReader is None:
            return f"[PDF附件：{file_name}] 已保存原文件，当前环境未安装 PDF 文本提取库，需后续补充 OCR/解析。"
        try:
            reader = PdfReader(io.BytesIO(payload))
            pages: list[str] = []
            for page in reader.pages:
                extracted = page.extract_text() or ""
                if extracted.strip():
                    pages.append(extracted.strip())
            if pages:
                return "\n\n".join(pages)
        except Exception:
            pass
        return f"[PDF附件：{file_name}] 已保留原文件，当前未能抽取正文文本，建议在后续做专业 PDF OCR。"

    def _extract_docx_text(self, file_name: str, payload: bytes) -> str:
        """抽取 Word 文本：读段落 + 表格（表格按行拼为 `a | b | c`），失败或缺库时返回占位文案。"""
        if DocxDocument is None:
            return f"[Word附件：{file_name}] 已保留原文件，当前环境未安装 docx 解析库。"
        try:
            document = DocxDocument(io.BytesIO(payload))
            paragraphs: list[str] = []
            for paragraph in document.paragraphs:
                text = paragraph.text.strip()
                if text:
                    paragraphs.append(text)
            for table in document.tables:
                for row in table.rows:
                    values = [cell.text.strip() for cell in row.cells]
                    joined = " | ".join(part for part in values if part)
                    if joined:
                        paragraphs.append(joined)
            text = "\n\n".join(paragraphs).strip()
            if text:
                return text
        except Exception:
            pass
        return f"[Word附件：{file_name}] 已保留原文件，当前无法读取正文内容，建议后续补充更强的文档抽取处理。"

    def _extract_image_text(self, file_name: str, payload: bytes) -> str:
        """图片抽取：先读取元数据；装有 tesseract 时尝试 OCR，否则只保留占位说明。"""
        if Image is None:
            return f"[图片附件：{file_name}] 已保留原文件，当前环境缺少图像处理库。"
        try:
            image = Image.open(io.BytesIO(payload))
            width, height = image.size
            info = image.format or "image"
            text = f"[图片附件：{file_name}]，格式={info}，尺寸={width}×{height}。当前仅保留原图与元数据，未完成 OCR。"
            try:
                import pytesseract  # type: ignore
                import shutil

                if shutil.which("tesseract"):
                    ocr_text = pytesseract.image_to_string(image)
                    clean = self.clean_text(ocr_text)
                    if clean:
                        return clean
            except Exception:
                pass
            return text
        except Exception:
            return f"[图片附件：{file_name}] 已保留原文件，但图像内容无法识别。"

    def clean_text(self, text: str) -> str:
        """清洗原始文本：规范化换行/全角空格/ASCII 控制字符、去噪音行、去连续重复空白行。"""
        normalized = (
            text.replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\u00a0", " ")
            .replace("\u200b", "")
            .replace("\ufeff", "")
        )
        normalized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", normalized)

        cleaned_lines: list[str] = []
        previous_line: str | None = None
        blank_pending = False
        for raw_line in normalized.split("\n"):
            line = re.sub(r"[ \t]+", " ", raw_line).strip()
            if self._is_noise_line(line):
                continue
            if not line:
                blank_pending = bool(cleaned_lines)
                continue
            if line == previous_line:
                continue
            if blank_pending:
                cleaned_lines.append("")
                blank_pending = False
            cleaned_lines.append(line)
            previous_line = line

        return "\n".join(cleaned_lines).strip()

    def segment(self, text: str) -> list[DocumentSegment]:
        """按空行切分文本为分段，并推断每段类型（field/heading/paragraph）。"""
        chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", text) if chunk.strip()]
        segments: list[DocumentSegment] = []
        for index, chunk in enumerate(chunks, start=1):
            field_name = self._detect_field_name(chunk)
            kind = "field" if field_name else "heading" if self._looks_like_heading(chunk) else "paragraph"
            segments.append(DocumentSegment(index=index, kind=kind, text=chunk, field_name=field_name))
        return segments

    def normalize_fields(self, segments: list[DocumentSegment]) -> dict[str, str]:
        """把「键：值」型分段规整为统一字段表（别名映射到规范键），供结构化入库。"""
        fields: dict[str, str] = {}
        aliases = {
            "title": {"标题", "需求标题", "名称", "需求名称", "title", "name"},
            "requester": {"提出人", "申请人", "提交人", "requester", "owner"},
            "department": {"部门", "业务部门", "department", "dept"},
            "business_domain": {"业务域", "领域", "业务领域", "domain"},
            "priority": {"优先级", "priority"},
            "sensitivity_level": {"敏感级别", "敏感等级", "security_level", "sensitivity"},
        }
        alias_to_field = {alias.lower(): field for field, names in aliases.items() for alias in names}

        for segment in segments:
            if segment.kind != "field":
                continue
            key, value = [part.strip() for part in re.split(r"[:：]", segment.text, maxsplit=1)]
            normalized_key = alias_to_field.get(key.lower())
            if normalized_key and value:
                fields[normalized_key] = value
        return fields

    def _fallback_from_name(self, file_name: str) -> str:
        """解析不到正文时，用文件名生成可读占位需求文案，保证文档不被丢弃。"""
        stem = Path(file_name).stem or "requirement-document"
        return f"需求文档：{stem}。请补充详细业务说明。"

    def _is_noise_line(self, line: str) -> bool:
        """判断是否页眉/页脚/分隔线等噪音行，清洗时跳过。"""
        if not line:
            return False
        patterns = (
            r"^-{3,}$",
            r"^={3,}$",
            r"^第\s*\d+\s*页\s*/\s*共\s*\d+\s*页$",
            r"^第\s*\d+\s*页$",
            r"^page\s+\d+(\s+of\s+\d+)?$",
            r"^confidential$",
        )
        return any(re.match(pattern, line, flags=re.IGNORECASE) for pattern in patterns)

    def _detect_field_name(self, text: str) -> str | None:
        """若单行文本形如「键：值」，返回键名；否则视为普通段落返回 None。"""
        if "\n" in text or not re.search(r"[:：]", text):
            return None
        key = re.split(r"[:：]", text, maxsplit=1)[0].strip()
        return key or None

    def _looks_like_heading(self, text: str) -> bool:
        """粗判是否像段落标题：单行、较短、不以句号结尾。"""
        return "\n" not in text and len(text) <= 40 and not text.endswith(("。", ".", "；", ";"))
