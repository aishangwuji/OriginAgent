"""Tests for context builder media handling."""

from __future__ import annotations

from pathlib import Path

from OriginAgent.agent.context import ContextBuilder
from OriginAgent.utils.document import extract_documents


def _make_builder(tmp_path: Path) -> ContextBuilder:
    """Create a minimal ContextBuilder for testing."""
    return ContextBuilder(workspace=tmp_path, timezone="UTC")


def _text_blocks(result: list[dict[str, object]]) -> list[str]:
    return [
        str(block.get("text", ""))
        for block in result
        if isinstance(block, dict) and block.get("type") == "text"
    ]


def test_build_user_content_with_no_media_returns_string(tmp_path: Path) -> None:
    builder = _make_builder(tmp_path)
    result = builder._build_user_content("hello", None)
    assert result == [{"type": "text", "text": "hello"}]


def test_build_user_content_with_image_returns_list(tmp_path: Path) -> None:
    """Image files should produce base64 content blocks."""
    builder = _make_builder(tmp_path)
    png = tmp_path / "test.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    result = builder._build_user_content("describe this", [str(png)])
    assert isinstance(result, list)
    types = [b["type"] for b in result]
    assert "image_url" in types
    assert "text" in types


def test_build_user_content_ignores_non_image_files(tmp_path: Path) -> None:
    """Non-image attachments should be preserved as provider-neutral refs."""
    builder = _make_builder(tmp_path)
    txt = tmp_path / "notes.txt"
    txt.write_text("some text", encoding="utf-8")
    result = builder._build_user_content("summarize", [str(txt)])
    assert any(block.get("type") == "attachment_ref" for block in result)
    assert result[-1] == {"type": "text", "text": "summarize"}


def test_build_user_content_mixed_image_and_non_image(tmp_path: Path) -> None:
    """Images stay native; non-image attachments are preserved as refs."""
    builder = _make_builder(tmp_path)
    png = tmp_path / "chart.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    txt = tmp_path / "report.txt"
    txt.write_text("report text", encoding="utf-8")

    result = builder._build_user_content("analyze", [str(png), str(txt)])
    assert isinstance(result, list)
    assert any(b["type"] == "image_url" for b in result)
    assert any(b["type"] == "attachment_ref" for b in result)
    text_parts = [b.get("text", "") for b in result if b.get("type") == "text"]
    assert all("report text" not in t for t in text_parts)


# ---------------------------------------------------------------------------
# Bug detection: extract_documents must be called BEFORE _build_user_content
# to prevent document media from being silently dropped.
# This simulates the _drain_pending code path.
# ---------------------------------------------------------------------------

def test_drain_pending_path_preserves_document_text(tmp_path: Path) -> None:
    """Simulates the _drain_pending path: a pending follow-up message
    with a document attachment must have its text extracted before being
    passed to _build_user_content.  Without extract_documents, the
    document is silently dropped."""
    from docx import Document

    doc = Document()
    doc.add_paragraph("Quarterly revenue is $5M")
    docx_path = tmp_path / "report.docx"
    doc.save(docx_path)

    content = "summarize"
    media = [str(docx_path)]

    # Step 1: extract_documents separates docs from images
    new_content, image_only = extract_documents(content, media)

    # Step 2: _build_user_content handles only images (none left here)
    builder = _make_builder(tmp_path)
    result = builder._build_user_content(new_content, image_only if image_only else None)

    # The document text should be present in the final content
    joined = "\n".join(_text_blocks(result))
    assert "Quarterly revenue" in joined
    assert "summarize" in joined


def test_drain_pending_path_without_extract_loses_document(tmp_path: Path) -> None:
    """Without extract_documents, inline-text docs are not expanded into text."""
    from docx import Document

    doc = Document()
    doc.add_paragraph("Secret data in document")
    docx_path = tmp_path / "report.docx"
    doc.save(docx_path)

    builder = _make_builder(tmp_path)

    # Bug path: call _build_user_content directly with document media
    result = builder._build_user_content("summarize", [str(docx_path)])

    # The document remains an attachment ref; inline document text is absent.
    assert any(block.get("type") == "attachment_ref" for block in result)
    assert all("Secret data" not in text for text in _text_blocks(result))


def test_build_user_content_preserves_pdf_as_attachment_ref(tmp_path: Path) -> None:
    builder = _make_builder(tmp_path)
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n")

    result = builder._build_user_content("read this", [str(pdf)])

    refs = [block for block in result if block.get("type") == "attachment_ref"]
    assert len(refs) == 1
    attachment = refs[0]["attachment"]
    assert attachment["kind"] == "document"
    assert attachment["path"] == str(pdf)
