"""Integration tests for the core PDF operations."""

from __future__ import annotations

import pytest
from pypdf import PdfReader

from pdfeditor.core import (
    combine_pdfs,
    delete_page_range,
    extract_pages,
    get_metadata,
    images_to_pdf,
    insert_pages,
    pdf_to_text,
    reorder_pages,
    rotate_pdf,
    set_metadata,
    split_pdf,
    watermark_pdf,
)


def test_rotate_pdf(make_pdf, tmp_path):
    src = make_pdf(pages=2)
    out = tmp_path / "rotated.pdf"
    rotate_pdf(src, out, 90, None)
    assert len(PdfReader(str(out)).pages) == 2


def test_rotate_pdf_requires_multiple_of_90(make_pdf, tmp_path):
    src = make_pdf()
    with pytest.raises(ValueError):
        rotate_pdf(src, tmp_path / "o.pdf", 45, None)


def test_delete_page_range(make_pdf, tmp_path):
    src = make_pdf(pages=3)
    out = tmp_path / "deleted.pdf"
    delete_page_range(src, out, "2")
    assert len(PdfReader(str(out)).pages) == 2


def test_delete_all_pages_raises(make_pdf, tmp_path):
    src = make_pdf(pages=2)
    with pytest.raises(ValueError):
        delete_page_range(src, tmp_path / "o.pdf", "1-2")


def test_reorder_pages(make_pdf, tmp_path):
    src = make_pdf(pages=3)
    out = tmp_path / "reordered.pdf"
    reorder_pages(src, out, "3,1,2")
    assert len(PdfReader(str(out)).pages) == 3


def test_split_pdf(make_pdf, tmp_path):
    src = make_pdf(pages=3)
    written = split_pdf(src, tmp_path / "split", None, None)
    assert len(written) == 3
    assert all(path.exists() for path in written)


def test_watermark_pdf(make_pdf, tmp_path):
    src = make_pdf(pages=1)
    out = tmp_path / "watermarked.pdf"
    watermark_pdf(src, out, "CONFIDENTIAL", 0.3, 48)
    assert out.exists()


def test_combine_pdfs(make_pdf, tmp_path):
    first = make_pdf("a.pdf", pages=1)
    second = make_pdf("b.pdf", pages=2)
    out = tmp_path / "combined.pdf"
    combine_pdfs([first, second], out)
    assert len(PdfReader(str(out)).pages) == 3


def test_pdf_to_text(make_pdf, tmp_path):
    src = make_pdf(pages=2)
    out = tmp_path / "text.txt"
    pdf_to_text(src, out)
    text = out.read_text(encoding="utf-8")
    assert "Page 1" in text
    assert "Page 2" in text


def test_images_to_pdf(make_image, tmp_path):
    first = make_image("a.png", (200, 100))
    second = make_image("b.png", (100, 200))
    out = tmp_path / "images.pdf"
    images_to_pdf([first, second], out)
    assert len(PdfReader(str(out)).pages) == 2


def test_images_to_pdf_requires_input(tmp_path):
    with pytest.raises(ValueError):
        images_to_pdf([], tmp_path / "x.pdf")


def test_extract_pages(make_pdf, tmp_path):
    src = make_pdf(pages=4)
    out = tmp_path / "extract.pdf"
    extract_pages(src, out, "2,4")
    assert len(PdfReader(str(out)).pages) == 2


def test_insert_pages(make_pdf, tmp_path):
    base = make_pdf("base.pdf", pages=2)
    extra = make_pdf("extra.pdf", pages=2)
    out = tmp_path / "inserted.pdf"
    insert_pages(base, extra, out, position=2, pages=None)
    assert len(PdfReader(str(out)).pages) == 4


def test_insert_pages_out_of_range(make_pdf, tmp_path):
    base = make_pdf("base.pdf", pages=2)
    extra = make_pdf("extra.pdf", pages=1)
    with pytest.raises(ValueError):
        insert_pages(base, extra, tmp_path / "o.pdf", position=99, pages=None)


def test_metadata_roundtrip(make_pdf, tmp_path):
    src = make_pdf(pages=1)
    out = tmp_path / "meta.pdf"
    set_metadata(src, out, {"Title": "My Report", "Author": "Me"})
    metadata = get_metadata(out)
    assert metadata.get("Title") == "My Report"
    assert metadata.get("Author") == "Me"
