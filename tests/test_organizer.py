"""Tests for the headless page-organizer session (no Qt involved)."""

from __future__ import annotations

import re

import pytest
from pypdf import PdfReader

from pdfeditor.core.organizer import PageOrganizerDocument


def _page_texts(path) -> list[str]:
    return [(page.extract_text() or "").strip() for page in PdfReader(str(path)).pages]


def _page_numbers(path) -> list[int]:
    """The ``Page N`` marker written by the make_pdf fixture, in document order."""
    numbers: list[int] = []
    for text in _page_texts(path):
        match = re.search(r"Page (\d+)", text)
        numbers.append(int(match.group(1)) if match else -1)
    return numbers


def test_organizer_reorder_leaves_source_alone(make_pdf):
    source = make_pdf(pages=3)
    with PageOrganizerDocument(source) as document:
        assert document.page_count() == 3
        assert document.is_dirty is False

        document.reorder("3,1,2")

        assert _page_numbers(document.path) == [3, 1, 2]
        assert document.is_dirty is True
        # The original file is never touched.
        assert _page_numbers(source) == [1, 2, 3]


def test_organizer_rotate_then_delete(make_pdf):
    source = make_pdf(pages=3)
    with PageOrganizerDocument(source) as document:
        document.rotate("1", 90)
        assert PdfReader(str(document.path)).pages[0].rotation == 90
        assert PdfReader(str(document.path)).pages[1].rotation == 0

        document.delete("2")
        assert document.page_count() == 2
        assert _page_numbers(document.path) == [1, 3]


def test_organizer_rotate_without_selection_covers_every_page(make_pdf):
    source = make_pdf(pages=2)
    with PageOrganizerDocument(source) as document:
        document.rotate(None, 180)
        assert [page.rotation for page in PdfReader(str(document.path)).pages] == [180, 180]


def test_organizer_watermark_only_marks_selected_pages(make_pdf):
    source = make_pdf(pages=3)
    with PageOrganizerDocument(source) as document:
        document.watermark("2", "CONFIDENTIAL", 0.3, 40)

        texts = _page_texts(document.path)
        assert "CONFIDENTIAL" not in texts[0]
        assert "CONFIDENTIAL" in texts[1]
        assert "CONFIDENTIAL" not in texts[2]
        assert document.page_count() == 3


def test_organizer_insert_adds_pages_where_asked(make_pdf):
    source = make_pdf(pages=3)
    extra = make_pdf("extra.pdf", pages=2)
    with PageOrganizerDocument(source) as document:
        document.insert(extra, position=1)

        assert document.page_count() == 5
        numbers = _page_numbers(document.path)
        # The two inserted pages lead, the three original pages follow in order.
        assert numbers == [1, 2, 1, 2, 3]


def test_organizer_extract_and_split_do_not_change_working_copy(make_pdf, tmp_path):
    source = make_pdf(pages=3)
    with PageOrganizerDocument(source) as document:
        extracted = document.extract("1,3", tmp_path / "picked.pdf")
        assert _page_numbers(extracted) == [1, 3]
        assert document.page_count() == 3

        written = document.split(tmp_path / "split", pages="2", prefix="part")
        assert [path.name for path in written] == ["part_page_002.pdf"]
        assert document.page_count() == 3
        assert document.is_dirty is False


def test_organizer_metadata_round_trip(make_pdf):
    source = make_pdf(pages=2)
    with PageOrganizerDocument(source) as document:
        document.set_metadata({"Title": "Quarterly Report", "Author": "QA"})

        metadata = document.get_metadata()
        assert metadata["Title"] == "Quarterly Report"
        assert metadata["Author"] == "QA"
        assert document.page_count() == 2


def test_organizer_save_as_revert_and_close(make_pdf, tmp_path):
    source = make_pdf(pages=3)
    document = PageOrganizerDocument(source)
    working_dir = document.path.parent

    document.reorder("3,2,1")
    destination = document.save_as(tmp_path / "out" / "organized.pdf")
    assert destination.is_file()
    assert document.saved_path == destination
    assert document.is_dirty is False
    assert _page_numbers(destination) == [3, 2, 1]

    document.delete("1")
    assert document.is_dirty is True
    assert document.saved_path is None

    document.revert()
    assert document.is_dirty is False
    assert _page_numbers(document.path) == [1, 2, 3]

    document.close()
    assert not working_dir.exists()


def test_organizer_close_keeps_a_caller_supplied_folder(make_pdf, tmp_path):
    source = make_pdf(pages=1)
    working_dir = tmp_path / "workspace"
    document = PageOrganizerDocument(source, working_dir=working_dir)
    document.rotate(None, 90)
    document.close()
    assert working_dir.is_dir()


def test_organizer_rejects_a_missing_source(tmp_path):
    with pytest.raises(FileNotFoundError):
        PageOrganizerDocument(tmp_path / "missing.pdf")


def test_organizer_suggests_an_output_name(make_pdf):
    source = make_pdf("report.pdf", pages=1)
    with PageOrganizerDocument(source) as document:
        assert document.suggest_output_name() == "report_organized.pdf"
