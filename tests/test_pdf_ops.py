"""Integration tests for the core PDF operations."""

from __future__ import annotations

import pytest
from pypdf import PdfReader

from pdfeditor.core import (
    EXPORT_CHUNKED,
    EXPORT_COMBINED,
    EXPORT_SINGLE,
    batch_rotate_pdfs,
    batch_split_pdfs,
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


def test_split_pdf_into_multipage_documents(make_pdf, tmp_path):
    src = make_pdf(pages=5)

    written = split_pdf(src, tmp_path / "chunks", None, None, chunk_size=2)

    assert [path.name for path in written] == [
        "input_pages_001-002.pdf",
        "input_pages_003-004.pdf",
        "input_pages_005-005.pdf",
    ]
    # Two pages, two pages, then the shorter final group.
    assert [len(PdfReader(str(path)).pages) for path in written] == [2, 2, 1]


def test_split_pdf_chunks_only_the_selected_pages(make_pdf, tmp_path):
    src = make_pdf(pages=6)

    written = split_pdf(src, tmp_path / "picked", "1,2,5,6", prefix="part", chunk_size=2)

    assert [path.name for path in written] == [
        "part_pages_001-002.pdf",
        "part_pages_005-006.pdf",
    ]


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


def test_extract_pages_without_a_spec_keeps_every_page(make_pdf, tmp_path):
    src = make_pdf(pages=3)
    out = tmp_path / "whole.pdf"

    extract_pages(src, out)

    assert len(PdfReader(str(out)).pages) == 3


def test_batch_split_pdfs_writes_one_file_per_page_by_default(make_pdf, tmp_path):
    make_pdf("report.pdf", pages=3)
    out = tmp_path / "out"

    created = batch_split_pdfs(tmp_path, out, None, None, "*.pdf", workers=1)

    assert created == 3
    # Everything lands in the output folder itself, named after its source PDF.
    assert sorted(path.name for path in out.glob("*.pdf")) == [
        "report_page_001.pdf",
        "report_page_002.pdf",
        "report_page_003.pdf",
    ]
    assert not any(path.is_dir() for path in out.iterdir())


def test_batch_split_pdfs_can_export_one_pdf_per_input_file(make_pdf, tmp_path):
    make_pdf("a.pdf", pages=2)
    make_pdf("b.pdf", pages=3)
    out = tmp_path / "out"

    created = batch_split_pdfs(tmp_path, out, "1", None, "*.pdf", workers=1, mode=EXPORT_SINGLE)

    assert created == 2
    # Like every other bulk operation, the file is named after its input PDF.
    assert sorted(path.name for path in out.glob("*.pdf")) == [
        "a_selected.pdf",
        "b_selected.pdf",
    ]
    assert len(PdfReader(str(out / "a_selected.pdf")).pages) == 1


def test_batch_split_pdfs_can_export_documents_of_n_pages(make_pdf, tmp_path):
    make_pdf("report.pdf", pages=10)
    out = tmp_path / "out"

    created = batch_split_pdfs(
        tmp_path, out, None, "part", "*.pdf", workers=1, mode=EXPORT_CHUNKED, chunk_size=6
    )

    assert created == 2
    written = sorted(out.glob("*.pdf"))
    assert [path.name for path in written] == ["part_pages_001-006.pdf", "part_pages_007-010.pdf"]
    assert [len(PdfReader(str(path)).pages) for path in written] == [6, 4]


def test_batch_split_pdfs_keeps_the_source_name_when_a_prefix_covers_several_files(
    make_pdf, tmp_path
):
    """A prefix alone would make two input PDFs write the same file, so the source sticks."""
    make_pdf("a.pdf", pages=2)
    make_pdf("b.pdf", pages=2)
    out = tmp_path / "out"

    created = batch_split_pdfs(tmp_path, out, None, "part", "*.pdf", workers=1)

    assert created == 4
    assert sorted(path.name for path in out.glob("*.pdf")) == [
        "part_a_page_001.pdf",
        "part_a_page_002.pdf",
        "part_b_page_001.pdf",
        "part_b_page_002.pdf",
    ]


def test_batch_split_pdfs_can_combine_every_file_into_one_pdf(make_pdf, tmp_path):
    make_pdf("a.pdf", pages=2)
    make_pdf("b.pdf", pages=3)
    out = tmp_path / "out"

    created = batch_split_pdfs(tmp_path, out, None, None, "*.pdf", workers=1, mode=EXPORT_COMBINED)

    assert created == 1
    # Every matching PDF contributes its pages, in filename order.
    assert [path.name for path in out.glob("*.pdf")] == ["combined_selected.pdf"]
    assert len(PdfReader(str(out / "combined_selected.pdf")).pages) == 5

    # The page spec applies to each input PDF, and the prefix names the single output.
    picked_out = tmp_path / "picked"
    batch_split_pdfs(tmp_path, picked_out, "1", "first", "*.pdf", workers=1, mode=EXPORT_COMBINED)
    assert len(PdfReader(str(picked_out / "first_selected.pdf")).pages) == 2


def test_batch_split_pdfs_rejects_an_unknown_mode(make_pdf, tmp_path):
    make_pdf(pages=1)

    with pytest.raises(ValueError):
        batch_split_pdfs(tmp_path, tmp_path / "out", None, None, "*.pdf", mode="nonsense")


def test_split_pdf_writes_unique_names_when_asked(make_pdf, tmp_path):
    src = make_pdf("report.pdf", pages=2)
    out = tmp_path / "split"
    out.mkdir()
    (out / "report_page_001.pdf").write_bytes(b"%PDF-original")

    written = split_pdf(src, out, None, None, unique_names=True)

    assert sorted(path.name for path in written) == [
        "report_page_001 (1).pdf",
        "report_page_002.pdf",
    ]
    assert (out / "report_page_001.pdf").read_bytes() == b"%PDF-original"


def test_batch_rotate_pdfs_writes_unique_names_when_asked(make_pdf, tmp_path):
    make_pdf("report.pdf", pages=2)
    out = tmp_path / "out"
    out.mkdir()
    (out / "report_rotated.pdf").write_bytes(b"%PDF-original")

    written = batch_rotate_pdfs(tmp_path, out, 90, None, "*.pdf", workers=1, unique_names=True)

    assert [path.name for path in written] == ["report_rotated (1).pdf"]
    assert (out / "report_rotated.pdf").read_bytes() == b"%PDF-original"


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
