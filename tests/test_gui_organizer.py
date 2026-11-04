"""Tests for the page-organizer dialog and the shared page grid widget.

Skipped automatically when PySide6/pytest-qt are unavailable, like the other GUI tests.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from pypdf import PdfReader  # noqa: E402
from PySide6.QtGui import QAction  # noqa: E402

from pdfeditor.gui import MainWindow  # noqa: E402
from pdfeditor.gui import organizer as org  # noqa: E402
from pdfeditor.gui.preview import qtpdf_available  # noqa: E402
from pdfeditor.gui.widgets import PageGridWidget  # noqa: E402


class _FakeFileDialog:
    """Stands in for QFileDialog so the tests never block on user input."""

    path = ""

    @staticmethod
    def getSaveFileName(*args, **kwargs):
        return (_FakeFileDialog.path, "")

    @staticmethod
    def getOpenFileName(*args, **kwargs):
        return (_FakeFileDialog.path, "")

    @staticmethod
    def getExistingDirectory(*args, **kwargs):
        return _FakeFileDialog.path


def _page_texts(path) -> list[str]:
    return [(page.extract_text() or "").strip() for page in PdfReader(str(path)).pages]


def _open(path, qtbot) -> org.PageOrganizerDialog:
    dialog = org.PageOrganizerDialog(str(path))
    qtbot.addWidget(dialog)
    dialog.confirm_destructive = False
    if qtpdf_available():
        qtbot.waitUntil(lambda: dialog.grid.count() > 0, timeout=5000)
    return dialog


def _needs_qtpdf() -> None:
    if not qtpdf_available():
        pytest.skip("QtPdf not available")


# -- the shared grid widget ----------------------------------------------------


def test_page_grid_moves_a_multi_selection_as_a_block(qtbot):
    grid = PageGridWidget(grid=True, multi_select=True)
    qtbot.addWidget(grid)
    for label in "ABCDE":
        grid.addItem(label)

    # A Ctrl-click style selection of two pages, dropped onto page 3.
    grid.item(0).setSelected(True)
    grid.item(3).setSelected(True)
    assert grid.selected_rows() == [0, 3]
    assert grid.move_rows_to([0, 3], 2) is True
    assert [grid.item(row).text() for row in range(grid.count())] == ["B", "C", "A", "D", "E"]
    # The moved block stays selected, so a continuing drag keeps tracking it.
    assert grid.selected_rows() == [2, 3]


def test_page_grid_ignores_drops_inside_the_selection(qtbot):
    grid = PageGridWidget(multi_select=True)
    qtbot.addWidget(grid)
    for label in "ABC":
        grid.addItem(label)

    assert grid.move_rows_to([1], 1) is False
    assert grid.move_rows_to([0], 2) is True
    assert [grid.item(row).text() for row in range(3)] == ["B", "C", "A"]


# -- the organizer dialog ------------------------------------------------------


def test_organizer_lists_pages_and_supports_ctrl_a(qtbot, make_pdf):
    _needs_qtpdf()
    dialog = _open(make_pdf(pages=4), qtbot)

    assert dialog.grid.count() == 4
    assert dialog.grid.selectionMode() == dialog.grid.SelectionMode.ExtendedSelection

    dialog.select_all()
    assert dialog.grid.selected_rows() == [0, 1, 2, 3]
    assert dialog.selected_spec() == "1-4"

    dialog.grid.clearSelection()
    dialog.grid.item(1).setSelected(True)
    dialog.grid.item(2).setSelected(True)
    assert dialog.selected_spec() == "2-3"


def test_organizer_context_menu_exposes_every_page_action(qtbot, make_pdf):
    _needs_qtpdf()
    dialog = _open(make_pdf(pages=2), qtbot)

    menu = dialog.build_actions_menu()
    titles = {action.text() for action in menu.actions() if not action.isSeparator()}
    assert titles == {
        "Select All Pages",
        "Rotate Left",
        "Rotate Right",
        "Rotate 180°",
        "Delete Pages",
        "Extract Selected to PDF…",
        "Insert Pages…",
        "Split Pages…",
        "Watermark…",
        "Metadata…",
        "Revert to Original",
        "Save As…",
    }

    # Delete/extract need a selection; rotate falls back to every page.
    assert dialog._actions["delete"].isEnabled() is False
    assert dialog._actions["rotate_left"].isEnabled() is True
    dialog.select_all()
    assert dialog._actions["delete"].isEnabled() is True


def test_organizer_rotates_and_deletes_selected_pages(qtbot, make_pdf):
    _needs_qtpdf()
    source = make_pdf(pages=3)
    dialog = _open(source, qtbot)

    dialog.grid.item(0).setSelected(True)
    dialog.rotate_selected(90)
    assert PdfReader(str(dialog.document.path)).pages[0].rotation == 90

    dialog.grid.item(0).setSelected(True)
    dialog.delete_selected()
    assert dialog.document.page_count() == 2
    assert "Deleted" in dialog.status.text()
    # The file on disk is never modified; everything happens on the working copy.
    assert len(PdfReader(str(source)).pages) == 3


def test_organizer_watermarks_only_the_selected_pages(qtbot, make_pdf, monkeypatch):
    _needs_qtpdf()
    dialog = _open(make_pdf(pages=3), qtbot)

    class _FakeWatermark:
        def __init__(self, selected_pages=False, parent=None):
            self._selected = selected_pages

        def exec(self):
            return 1

        def values(self):
            return ("CONFIDENTIAL", 0.3, 40)

        def only_selected(self):
            return self._selected

    monkeypatch.setattr(org, "WatermarkDialog", _FakeWatermark)
    dialog.grid.item(1).setSelected(True)
    dialog.apply_watermark()

    texts = _page_texts(dialog.document.path)
    assert "CONFIDENTIAL" not in texts[0]
    assert "CONFIDENTIAL" in texts[1]
    assert "CONFIDENTIAL" not in texts[2]


def test_organizer_edits_metadata(qtbot, make_pdf, monkeypatch):
    dialog = _open(make_pdf(pages=2), qtbot)

    class _FakeMetadata:
        def __init__(self, metadata, parent=None):
            self.seen = dict(metadata)

        def exec(self):
            return 1

        def values(self):
            return {"Title": "Field Notes", "Author": "QA"}

    monkeypatch.setattr(org, "MetadataDialog", _FakeMetadata)
    dialog.edit_metadata()

    metadata = dialog.document.get_metadata()
    assert metadata["Title"] == "Field Notes"
    assert metadata["Author"] == "QA"


def test_organizer_extracts_splits_and_saves(qtbot, make_pdf, tmp_path, monkeypatch):
    _needs_qtpdf()
    dialog = _open(make_pdf(pages=3), qtbot)
    monkeypatch.setattr(org, "QFileDialog", _FakeFileDialog)

    _FakeFileDialog.path = str(tmp_path / "picked.pdf")
    dialog.grid.item(2).setSelected(True)
    dialog.extract_selected()
    assert len(PdfReader(str(tmp_path / "picked.pdf")).pages) == 1

    # With nothing selected, Split covers every page.
    _FakeFileDialog.path = str(tmp_path / "split")
    dialog.grid.clearSelection()
    dialog.split_pages()
    assert len(list((tmp_path / "split").glob("*.pdf"))) == 3

    _FakeFileDialog.path = str(tmp_path / "final.pdf")
    assert dialog.save_as() == str(tmp_path / "final.pdf")
    assert dialog.saved_path() == str(tmp_path / "final.pdf")
    assert dialog.document.is_dirty is False
    assert len(PdfReader(str(tmp_path / "final.pdf")).pages) == 3


def test_organizer_revert_restores_the_original(qtbot, make_pdf):
    _needs_qtpdf()
    dialog = _open(make_pdf(pages=3), qtbot)

    dialog.grid.item(0).setSelected(True)
    dialog.delete_selected()
    assert dialog.document.page_count() == 2

    dialog.revert()
    assert dialog.document.page_count() == 3
    assert dialog.grid.count() == 3
    assert "Reverted" in dialog.status.text()


def test_organizer_close_removes_the_working_copy(qtbot, make_pdf):
    _needs_qtpdf()
    dialog = _open(make_pdf(pages=1), qtbot)
    working_dir = dialog.document.path.parent
    assert working_dir.is_dir()

    dialog.close()
    assert not working_dir.exists()


def test_main_window_menu_opens_the_page_organizer(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    organise = next(
        action for action in window.findChildren(QAction) if action.text() == "&Organize Pages..."
    )
    assert organise.shortcut().toString() == "Ctrl+Shift+O"
    assert callable(window.open_page_organizer)
