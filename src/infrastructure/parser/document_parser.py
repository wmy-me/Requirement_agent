"""Document parser for requirement files and attachments."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ParsedDocument:
    title: str
    content: str
    extension: str
    pages: int = 1


class DocumentParser:
    """Parses uploaded text-based files into plain requirement text."""

    def parse(self, file_name: str, payload: bytes) -> ParsedDocument:
        text = payload.decode("utf-8", errors="ignore").strip()
        if not text:
            text = self._fallback_from_name(file_name)
        title = Path(file_name).stem or "requirement-document"
        extension = Path(file_name).suffix.lower() or ".txt"
        pages = max(1, len(text.splitlines()) // 25 + 1)
        return ParsedDocument(title=title, content=text, extension=extension, pages=pages)

    def _fallback_from_name(self, file_name: str) -> str:
        stem = Path(file_name).stem or "requirement-document"
        return f"需求文档：{stem}。请补充详细业务说明。"
