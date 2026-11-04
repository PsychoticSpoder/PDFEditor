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


def test_organizer_defers_writes_until_a_file_is_needed(make_pdf):
    """Edits are tracked in memory; the whole-document rewrite waits until a file is asked for."""
    source = make_pdf(pages=4)
    with PageOrganizerDocument(source) as document:
        document.rotate("1", 90)
        document.delete("4")

        assert document.page_count() == 3  # tracked without touching the file
        assert len(document._pending) == 2

        assert _page_numbers(document.path) == [1, 2, 3]  # reading `.path` writes them
        assert document._pending == []
        assert PdfReader(str(document.path)).pages[0].rotation == 90


def test_organizer_writes_every_pending_edit_in_one_pass(make_pdf, monkeypatch):
    """A run of edits costs a single rewrite, not one per edit."""
    from pdfeditor.core import organizer as core_organizer

    rewrites: list[object] = []
    real_write = core_organizer._atomic_write_pdf

    def spy(writer, path):
        rewrites.append(path)
        return real_write(writer, path)

    monkeypatch.setattr(core_organizer, "_atomic_write_pdf", spy)

    source = make_pdf(pages=4)
    with PageOrganizerDocument(source) as document:
        document.rotate("2", 90)
        document.reorder("4,1,3,2")
        document.delete("1")
        document.set_metadata({"Title": "Combined"})

        assert rewrites == []
        assert _page_numbers(document.path) == [1, 3, 2]
        assert len(rewrites) == 1


def test_organizer_composes_edits_like_the_eager_core_operations(make_pdf):
    """The single-pass rewrite produces the same document as the step-by-step core calls."""
    source = make_pdf(pages=4)
    extra = make_pdf("extra.pdf", pages=2)

    with PageOrganizerDocument(source) as document:
        document.rotate("2", 90)
        document.reorder("4,1,3,2")
        document.delete("1")
        document.insert(extra, position=2, pages="1")
        document.set_metadata({"Title": "Combined"})

        numbers = _page_numbers(document.path)
        rotations = [page.rotation for page in PdfReader(str(document.path)).pages]
        metadata = document.get_metadata()

    assert numbers == [1, 1, 3, 2]
    # The page that started life as page 2 kept its rotation through the reorder/delete and
    # ended up last, which is exactly where the composed plan put it.
    assert rotations == [0, 0, 0, 90]
    assert metadata["Title"] == "Combined"


def test_organizer_reports_a_delete_of_every_page_immediately(make_pdf):
    source = make_pdf(pages=2)
    with PageOrganizerDocument(source) as document:
        with pytest.raises(ValueError, match="removed all pages"):
            document.delete("1-2")
        assert document.page_count() == 2


def test_organizer_suggests_an_output_name(make_pdf):
    source = make_pdf("report.pdf", pages=1)
    with PageOrganizerDocument(source) as document:
        assert document.suggest_output_name() == "report_organized.pdf"
