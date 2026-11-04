"""Tests for the page-organizer widget and the shared page grid widget.

Skipped automatically when PySide6/pytest-qt are unavailable, like the other GUI tests.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from pypdf import PdfReader  # noqa: E402
from PySide6.QtCore import QSize, QSizeF  # noqa: E402
from PySide6.QtGui import QAction, QImage  # noqa: E402
from PySide6.QtWidgets import QListWidgetItem  # noqa: E402

from pdfeditor.gui import MainWindow  # noqa: E402
from pdfeditor.gui import organizer as org  # noqa: E402
from pdfeditor.gui.preview import _ThumbnailWorker, qtpdf_available  # noqa: E402
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


def _page_numbers(path) -> list[int]:
    """The ``Page N`` marker written by the make_pdf fixture, in document order."""
    numbers: list[int] = []
    for text in _page_texts(path):
        match = re.search(r"Page (\d+)", text)
        numbers.append(int(match.group(1)) if match else -1)
    return numbers


def _open(path, qtbot) -> org.PageOrganizerWidget:
    """The organizer widget (the very same one the Single PDF Editing panel embeds)."""
    widget = org.PageOrganizerWidget(str(path))
    qtbot.addWidget(widget)
    widget.confirm_destructive = False
    if qtpdf_available():
        qtbot.waitUntil(lambda: widget.grid.count() > 0, timeout=5000)
    return widget


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


# -- the organizer widget ------------------------------------------------------


def test_organizer_lists_pages_and_supports_ctrl_a(qtbot, make_pdf):
    _needs_qtpdf()
    widget = _open(make_pdf(pages=4), qtbot)

    assert widget.grid.count() == 4
    assert widget.grid.selectionMode() == widget.grid.SelectionMode.ExtendedSelection

    widget.select_all()
    assert widget.grid.selected_rows() == [0, 1, 2, 3]
    assert widget.selected_spec() == "1-4"

    widget.grid.clearSelection()
    widget.grid.item(1).setSelected(True)
    widget.grid.item(2).setSelected(True)
    assert widget.selected_spec() == "2-3"


def test_organizer_context_menu_exposes_every_page_action(qtbot, make_pdf):
    _needs_qtpdf()
    widget = _open(make_pdf(pages=2), qtbot)

    menu = widget.build_actions_menu()
    titles = {action.text() for action in menu.actions() if not action.isSeparator()}
    assert titles == {
        "Select All Pages",
        "Rotate Left",
        "Rotate Right",
        "Rotate 180°",
        "Delete Pages",
        "Export Pages…",
        "Insert Pages…",
        "Watermark…",
        "Metadata…",
        "Revert to Original",
        "Save As…",
    }

    # Delete/extract need a selection; rotate falls back to every page.
    assert widget._actions["delete"].isEnabled() is False
    assert widget._actions["rotate_left"].isEnabled() is True
    widget.select_all()
    assert widget._actions["delete"].isEnabled() is True


def test_organizer_rotates_and_deletes_selected_pages(qtbot, make_pdf):
    _needs_qtpdf()
    source = make_pdf(pages=3)
    widget = _open(source, qtbot)

    widget.grid.item(0).setSelected(True)
    widget.rotate_selected(90)
    assert PdfReader(str(widget.document.path)).pages[0].rotation == 90

    widget.grid.item(0).setSelected(True)
    widget.delete_selected()
    assert widget.document.page_count() == 2
    assert "Deleted" in widget.status.text()
    # The file on disk is never modified; everything happens on the working copy.
    assert len(PdfReader(str(source)).pages) == 3


def test_organizer_watermarks_only_the_selected_pages(qtbot, make_pdf, monkeypatch):
    _needs_qtpdf()
    widget = _open(make_pdf(pages=3), qtbot)

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
    widget.grid.item(1).setSelected(True)
    widget.apply_watermark()

    texts = _page_texts(widget.document.path)
    assert "CONFIDENTIAL" not in texts[0]
    assert "CONFIDENTIAL" in texts[1]
    assert "CONFIDENTIAL" not in texts[2]


def test_organizer_edits_metadata(qtbot, make_pdf, monkeypatch):
    widget = _open(make_pdf(pages=2), qtbot)

    class _FakeMetadata:
        def __init__(self, metadata, parent=None):
            self.seen = dict(metadata)

        def exec(self):
            return 1

        def values(self):
            return {"Title": "Field Notes", "Author": "QA"}

    monkeypatch.setattr(org, "MetadataDialog", _FakeMetadata)
    widget.edit_metadata()

    metadata = widget.document.get_metadata()
    assert metadata["Title"] == "Field Notes"
    assert metadata["Author"] == "QA"


def test_organizer_exports_single_and_per_page_and_saves(qtbot, make_pdf, tmp_path, monkeypatch):
    _needs_qtpdf()
    widget = _open(make_pdf(pages=3), qtbot)
    monkeypatch.setattr(org, "QFileDialog", _FakeFileDialog)

    _FakeFileDialog.path = str(tmp_path / "picked.pdf")
    widget.grid.item(2).setSelected(True)
    widget.export_single("3")
    assert len(PdfReader(str(tmp_path / "picked.pdf")).pages) == 1

    # With nothing selected, a per-page export covers every page.
    _FakeFileDialog.path = str(tmp_path / "split")
    widget.grid.clearSelection()
    widget.export_per_page()
    assert len(list((tmp_path / "split").glob("*.pdf"))) == 3

    _FakeFileDialog.path = str(tmp_path / "final.pdf")
    assert widget.save_as() == str(tmp_path / "final.pdf")
    assert widget.saved_path() == str(tmp_path / "final.pdf")
    assert widget.document.is_dirty is False
    assert len(PdfReader(str(tmp_path / "final.pdf")).pages) == 3


def test_organizer_revert_restores_the_original(qtbot, make_pdf):
    _needs_qtpdf()
    widget = _open(make_pdf(pages=3), qtbot)

    widget.grid.item(0).setSelected(True)
    widget.delete_selected()
    assert widget.document.page_count() == 2

    widget.revert()
    assert widget.document.page_count() == 3
    assert widget.grid.count() == 3
    assert "Reverted" in widget.status.text()


def test_organizer_close_removes_the_working_copy(qtbot, make_pdf):
    _needs_qtpdf()
    widget = _open(make_pdf(pages=1), qtbot)
    working_dir = widget.document.path.parent
    assert working_dir.is_dir()

    widget.close_document()
    assert not working_dir.exists()


def test_main_window_menu_opens_the_page_organizer(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    organise = next(
        action for action in window.findChildren(QAction) if action.text() == "&Organize Pages..."
    )
    assert organise.shortcut().toString() == "Ctrl+Shift+O"
    assert callable(window.open_page_organizer)


# -- incremental thumbnails ----------------------------------------------------


def test_page_grid_cells_share_one_height_so_labels_line_up(qtbot):
    grid = PageGridWidget(grid=True, multi_select=True)
    qtbot.addWidget(grid)
    portrait = QListWidgetItem("Page 1")
    landscape = QListWidgetItem("Page 2")
    grid.addItem(portrait)
    grid.addItem(landscape)

    grid.set_thumbnail(portrait, QImage(QSize(110, 142), QImage.Format.Format_RGB32))
    grid.set_thumbnail(landscape, QImage(QSize(142, 110), QImage.Format.Format_RGB32))

    # Same height => every caption sits on the same baseline; only the width differs.
    assert portrait.sizeHint().height() == landscape.sizeHint().height()
    assert landscape.sizeHint().width() > portrait.sizeHint().width()


def test_organizer_rotating_turns_the_cached_preview(qtbot, make_pdf):
    _needs_qtpdf()
    widget = _open(make_pdf(pages=2), qtbot)
    qtbot.waitUntil(lambda: len(widget._images) == 2, timeout=5000)
    before = widget._images[widget._keys[0]].size()

    widget.grid.item(0).setSelected(True)
    widget.rotate_selected(90)

    # Nothing is re-rendered: the stored preview is simply turned on its side.
    assert len(widget._images) == 2
    after = widget._images[widget._keys[0]].size()
    assert (after.width(), after.height()) == (before.height(), before.width())
    assert widget.grid.item(0).icon().isNull() is False
    assert widget.status.text().startswith("Rotated")


def test_organizer_metadata_keeps_every_thumbnail(qtbot, make_pdf, monkeypatch):
    _needs_qtpdf()
    widget = _open(make_pdf(pages=2), qtbot)
    qtbot.waitUntil(lambda: len(widget._images) == 2, timeout=5000)

    class _FakeMetadata:
        def __init__(self, metadata, parent=None):
            pass

        def exec(self):
            return 1

        def values(self):
            return {"Title": "Cached"}

    monkeypatch.setattr(org, "MetadataDialog", _FakeMetadata)
    widget.edit_metadata()

    # Metadata changes how the document reads, not how pages look.
    assert widget.document.get_metadata()["Title"] == "Cached"
    assert len(widget._images) == 2
    assert all(not widget.grid.item(row).icon().isNull() for row in range(2))


def test_organizer_drag_commits_the_new_order(qtbot, make_pdf):
    _needs_qtpdf()
    widget = _open(make_pdf(pages=3), qtbot)
    qtbot.waitUntil(lambda: len(widget._images) == 3, timeout=5000)

    # Move page 1 to the end the way a drag does, then commit it.
    assert widget.grid.move_rows_to([0], 2) is True
    widget.commit_order()

    assert _page_numbers(widget.document.path) == [2, 3, 1]
    # Reordering only moves pages, so nothing had to be rendered again.
    assert len(widget._images) == 3
    assert all(not widget.grid.item(row).icon().isNull() for row in range(3))
    assert [widget.grid.item(row).text() for row in range(3)] == ["Page 1", "Page 2", "Page 3"]


def test_export_pages_asks_between_one_pdf_and_one_per_page(qtbot, make_pdf, tmp_path, monkeypatch):
    _needs_qtpdf()
    widget = _open(make_pdf(pages=3), qtbot)
    monkeypatch.setattr(org, "QFileDialog", _FakeFileDialog)
    monkeypatch.setattr(org.ExportPagesDialog, "exec", lambda self: 1)

    # "One PDF" mode asks for a filename.
    monkeypatch.setattr(org.ExportPagesDialog, "single_pdf", lambda self: True)
    _FakeFileDialog.path = str(tmp_path / "all.pdf")
    widget.export_pages()
    assert len(PdfReader(str(tmp_path / "all.pdf")).pages) == 3

    # "One per page" mode asks for a folder instead.
    monkeypatch.setattr(org.ExportPagesDialog, "single_pdf", lambda self: False)
    _FakeFileDialog.path = str(tmp_path / "each")
    widget.export_pages()
    assert len(list((tmp_path / "each").glob("*.pdf"))) == 3


def test_export_dialog_defaults_to_one_pdf(qtbot):
    export_dialog = org.ExportPagesDialog("page(s) 1-3")
    qtbot.addWidget(export_dialog)

    assert export_dialog.single_pdf() is True
    export_dialog.per_page_button.setChecked(True)
    assert export_dialog.single_pdf() is False


# -- thumbnails follow each page's own shape -----------------------------------


def test_page_grid_thumbnail_keeps_the_page_aspect(qtbot):
    grid = PageGridWidget(grid=True, multi_select=True)
    qtbot.addWidget(grid)
    item = QListWidgetItem("Page 1")
    grid.addItem(item)

    grid.set_thumbnail(item, QImage(QSize(142, 110), QImage.Format.Format_RGB32))
    landscape = item.sizeHint()

    grid.set_thumbnail(item, QImage(QSize(110, 142), QImage.Format.Format_RGB32))
    portrait = item.sizeHint()

    # The cell follows the page's own width, and both share the one cell height (which is
    # what keeps the "Page N" captions on a single line).
    assert landscape.width() > portrait.width()
    assert landscape.height() == portrait.height() == PageGridWidget.cell_height()


def test_thumbnail_worker_fits_each_page_to_its_own_aspect():
    class _Document:
        def pagePointSize(self, index):
            # Rotated pages are already landscape here: QPdfDocument.pagePointSize()
            # takes the page's /Rotate into account.
            return QSizeF(612, 792) if index == 0 else QSizeF(792, 612)

    worker = _ThumbnailWorker("unused.pdf", QSize(100, 100))
    portrait = worker._render_size(_Document(), 0)
    landscape = worker._render_size(_Document(), 1)

    assert portrait.height() > portrait.width()
    assert landscape.width() > landscape.height()
    assert max(portrait.width(), portrait.height()) <= 100
    assert max(landscape.width(), landscape.height()) <= 100
