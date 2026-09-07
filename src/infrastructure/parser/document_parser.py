"""Document parser for requirement files and attachments."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class DocumentSegment:
    index: int
    kind: str
    text: str
    field_name: str | None = None


@dataclass(slots=True)
class ParsedDocument:
    title: str
    content: str
    raw_content: str
    extension: str
    pages: int = 1
    segments: list[DocumentSegment] | None = None
    normalized_fields: dict[str, str] | None = None


class DocumentParser:
    """Parses uploaded text-based files into plain requirement text."""

    def parse(self, file_name: str, payload: bytes) -> ParsedDocument:
        raw_text = payload.decode("utf-8", errors="ignore").strip()
        if not raw_text:
            raw_text = self._fallback_from_name(file_name)
        text = self.clean_text(raw_text)
        segments = self.segment(text)
        normalized_fields = self.normalize_fields(segments)
        title = Path(file_name).stem or "requirement-document"
        extension = Path(file_name).suffix.lower() or ".txt"
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

    def clean_text(self, text: str) -> str:
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
        chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", text) if chunk.strip()]
        segments: list[DocumentSegment] = []
        for index, chunk in enumerate(chunks, start=1):
            field_name = self._detect_field_name(chunk)
            kind = "field" if field_name else "heading" if self._looks_like_heading(chunk) else "paragraph"
            segments.append(DocumentSegment(index=index, kind=kind, text=chunk, field_name=field_name))
        return segments

    def normalize_fields(self, segments: list[DocumentSegment]) -> dict[str, str]:
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
        stem = Path(file_name).stem or "requirement-document"
        return f"需求文档：{stem}。请补充详细业务说明。"

    def _is_noise_line(self, line: str) -> bool:
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
        if "\n" in text or not re.search(r"[:：]", text):
            return None
        key = re.split(r"[:：]", text, maxsplit=1)[0].strip()
        return key or None

    def _looks_like_heading(self, text: str) -> bool:
        return "\n" not in text and len(text) <= 40 and not text.endswith(("。", ".", "；", ";"))
