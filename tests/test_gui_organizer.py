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
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QDialog, QListWidgetItem, QMessageBox  # noqa: E402

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


def test_page_grid_zoom_scales_the_thumbnail_box(qtbot):
    grid = PageGridWidget(grid=True, multi_select=True)
    qtbot.addWidget(grid)

    assert grid.thumbnail_scale() == 1.0
    assert grid.thumbnail_box() == QSize(160, 142)

    grid.set_thumbnail_scale(2.0)
    assert grid.thumbnail_scale() == 2.0
    assert grid.thumbnail_box() == QSize(320, 284)

    # Values outside the supported window are clamped rather than rejected.
    grid.set_thumbnail_scale(10.0)
    assert grid.thumbnail_scale() == 5.0
    assert grid.thumbnail_box() == QSize(800, 710)
    grid.set_thumbnail_scale(0.1)
    assert grid.thumbnail_scale() == 1.0


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


# -- thumbnail zoom ------------------------------------------------------------


def test_organizer_zoom_slider_drives_the_grid_scale(qtbot, make_pdf):
    widget = _open(make_pdf(pages=2), qtbot)

    assert widget.zoom_slider.value() == 100
    widget.zoom_slider.setValue(150)
    # The label follows at once; the cells resize once the value has settled.
    assert widget.zoom_label.text() == "150%"
    qtbot.waitUntil(lambda: widget.grid.thumbnail_scale() == 1.5, timeout=2000)


def test_organizer_zoom_drag_only_previews_the_label(qtbot, make_pdf):
    widget = _open(make_pdf(pages=2), qtbot)

    # Dragging is deliberately cheap: tracking is off, and a drag only moves the label —
    # the grid is left untouched until the value settles (which keeps dragging smooth).
    assert widget.zoom_slider.hasTracking() is False
    widget.zoom_slider.sliderMoved.emit(300)
    assert widget.zoom_label.text() == "300%"
    assert widget.grid.thumbnail_scale() == 1.0


def test_organizer_zoom_commits_only_when_the_handle_is_released(qtbot, make_pdf):
    """Dragging the slider must not re-render anything until the handle is let go."""
    widget = _open(make_pdf(pages=2), qtbot)

    # A real drag emits sliderPressed and then sliderMoved over and over — never valueChanged.
    widget.zoom_slider.sliderPressed.emit()
    widget.zoom_slider.sliderMoved.emit(400)
    qtbot.wait(200)
    assert widget.zoom_label.text() == "400%"
    assert widget.grid.thumbnail_scale() == 1.0  # nothing resized or re-rendered yet

    # Letting go applies the position and re-renders once, at the chosen size.
    widget.zoom_slider.setSliderPosition(400)
    widget.zoom_slider.sliderReleased.emit()
    assert widget.grid.thumbnail_scale() == 4.0


def test_organizer_zoom_ignores_a_click_without_moving_the_slider(qtbot, make_pdf, monkeypatch):
    """Grabbing and letting go without moving must not trigger a pointless re-render."""
    widget = _open(make_pdf(pages=2), qtbot)
    rerenders: list[int] = []
    monkeypatch.setattr(widget, "_rerender_thumbnails", lambda: rerenders.append(1))

    widget.zoom_slider.sliderPressed.emit()
    widget.zoom_slider.sliderReleased.emit()

    assert rerenders == []
    assert widget.grid.thumbnail_scale() == 1.0


def test_organizer_zoom_defers_the_commit_while_the_button_is_held(qtbot, make_pdf, monkeypatch):
    """Pressing the bar changes the value at once; nothing may re-render until it is released."""
    widget = _open(make_pdf(pages=2), qtbot)
    rerenders: list[int] = []
    monkeypatch.setattr(widget, "_rerender_thumbnails", lambda: rerenders.append(1))
    monkeypatch.setattr(widget, "_zoom_held", lambda: True)

    widget.zoom_slider.setValue(200)  # what pressing the bar does, with no press/release signals
    qtbot.wait(300)  # well past the debounce
    assert rerenders == []
    assert widget.grid.thumbnail_scale() == 1.0

    # Releasing the button lets the postponed commit through, at the chosen size.
    monkeypatch.setattr(widget, "_zoom_held", lambda: False)
    qtbot.waitUntil(lambda: rerenders == [1], timeout=2000)
    assert widget.grid.thumbnail_scale() == 2.0


def test_organizer_zoom_applies_a_click_on_the_bar(qtbot, make_pdf):
    """Clicking the bar must still apply the zoom, even though it sets the value on press."""
    widget = _open(make_pdf(pages=2), qtbot)

    # Qt delivers the bar's new value first, then the press/release pair for the handle grab.
    widget.zoom_slider.setValue(300)
    widget.zoom_slider.sliderPressed.emit()
    widget.zoom_slider.sliderReleased.emit()

    assert widget.grid.thumbnail_scale() == 3.0


def test_page_grid_zoom_sizes_cells_from_the_page_aspect(qtbot):
    """A zoom must size each cell from the page's *aspect ratio*, not its pixel size.

    Deriving the width from the raw pixmap size left a cell at its old, small width until the
    bigger image arrived, so the zoomed grid was laid out with stale offsets and the pages
    overlapped. Fitting the aspect ratio into the new box is correct immediately and stays
    stable while the re-rendered images stream in.
    """
    grid = PageGridWidget(grid=True, multi_select=True)
    qtbot.addWidget(grid)
    portrait = QListWidgetItem("Page 1")
    landscape = QListWidgetItem("Page 2")
    grid.addItem(portrait)
    grid.addItem(landscape)
    grid.set_thumbnail(portrait, QImage(QSize(110, 142), QImage.Format.Format_RGB32))
    grid.set_thumbnail(landscape, QImage(QSize(142, 110), QImage.Format.Format_RGB32))

    grid.set_thumbnail_scale(4.0)
    # The box is 640x568 at 400%; the aspect ratio decides each width (6px padding per side).
    assert grid.thumbnail_box() == QSize(640, 568)
    assert portrait.sizeHint().width() == round(568 * 110 / 142) + 12
    assert landscape.sizeHint().width() == 640 + 12


def test_page_grid_zoom_relayouts_without_overlapping_cells(qtbot):
    """Zooming after the pages exist must recompute the flow positions, not keep stale ones."""
    grid = PageGridWidget(grid=True, multi_select=True)
    grid.resize(1200, 800)
    qtbot.addWidget(grid)
    for index in range(6):
        item = QListWidgetItem(f"Page {index + 1}")
        grid.addItem(item)
        size = QSize(110, 142) if index % 2 else QSize(142, 110)
        grid.set_thumbnail(item, QImage(size, QImage.Format.Format_RGB32))
    qtbot.wait(20)

    grid.set_thumbnail_scale(4.0)
    rects = [grid.visualItemRect(grid.item(row)) for row in range(6)]
    assert all(rect.width() > 0 and rect.height() > 0 for rect in rects)
    for first in range(len(rects)):
        for second in range(first + 1, len(rects)):
            assert not rects[first].intersects(rects[second]), (rects[first], rects[second])


def test_organizer_zoom_re_renders_larger_thumbnails(qtbot, make_pdf):
    _needs_qtpdf()
    widget = _open(make_pdf(pages=1), qtbot)
    qtbot.waitUntil(lambda: len(widget._images) == 1, timeout=5000)
    before = widget._images[widget._keys[0]].size()

    widget.set_thumbnail_scale(200)
    assert widget.grid.thumbnail_box() == QSize(320, 284)

    # The cached thumbnail is replaced by a bigger one rendered at the new resolution.
    qtbot.waitUntil(lambda: len(widget._images) == 1, timeout=5000)
    after = widget._images[widget._keys[0]].size()
    assert after.width() > before.width()
    assert after.height() > before.height()


# -- lazy rendering ------------------------------------------------------------


def test_organizer_renders_only_the_visible_pages_of_a_big_document(qtbot, make_pdf):
    """A big document renders lazily: pages off screen keep their caption until scrolled to."""
    _needs_qtpdf()
    widget = org.PageOrganizerWidget()
    widget.resize(900, 600)
    qtbot.addWidget(widget)
    widget.show()
    widget.load(make_pdf(pages=120))

    qtbot.waitUntil(lambda: len(widget._images) > 0, timeout=5000)
    qtbot.wait(250)  # let the debounced view timer settle
    assert 0 < len(widget._images) < 120

    # A page on screen is drawn; a page far below has not even been rendered.
    assert widget.grid.item(0).icon().isNull() is False
    assert widget.grid.item(119).icon().isNull() is True


def test_organizer_renders_a_small_document_whole(qtbot, make_pdf):
    """Below the lazy threshold every page is rendered, exactly as before."""
    _needs_qtpdf()
    widget = _open(make_pdf(pages=6), qtbot)
    qtbot.waitUntil(lambda: len(widget._images) == 6, timeout=5000)
    assert all(not widget.grid.item(row).icon().isNull() for row in range(6))


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


def test_export_pages_asks_how_to_split_the_selection(qtbot, make_pdf, tmp_path, monkeypatch):
    _needs_qtpdf()
    widget = _open(make_pdf(pages=4), qtbot)
    monkeypatch.setattr(org, "QFileDialog", _FakeFileDialog)
    monkeypatch.setattr(org.ExportPagesDialog, "exec", lambda self: 1)

    # "One PDF" mode asks for a filename.
    monkeypatch.setattr(org.ExportPagesDialog, "mode", lambda self: "single")
    _FakeFileDialog.path = str(tmp_path / "all.pdf")
    widget.export_pages()
    assert len(PdfReader(str(tmp_path / "all.pdf")).pages) == 4

    # "One per page" mode asks for a folder instead.
    monkeypatch.setattr(org.ExportPagesDialog, "mode", lambda self: "per-page")
    _FakeFileDialog.path = str(tmp_path / "each")
    widget.export_pages()
    assert len(list((tmp_path / "each").glob("*.pdf"))) == 4

    # "Documents of N pages" mode writes multipage chunks into a folder.
    monkeypatch.setattr(org.ExportPagesDialog, "mode", lambda self: "chunked")
    monkeypatch.setattr(org.ExportPagesDialog, "chunk_size", lambda self: 2)
    _FakeFileDialog.path = str(tmp_path / "chunks")
    widget.export_pages()
    chunked = sorted((tmp_path / "chunks").glob("*.pdf"))
    assert [len(PdfReader(str(path)).pages) for path in chunked] == [2, 2]


def test_export_dialog_offers_all_three_splits(qtbot):
    export_dialog = org.ExportPagesDialog("page(s) 1-3")
    qtbot.addWidget(export_dialog)

    # One combined PDF is the default.
    assert export_dialog.mode() == "single"
    assert export_dialog.selector.single_button.text() == "One PDF"
    # Combining several PDFs is a folder-export idea, so this dialog leaves it out.
    assert export_dialog.selector.combined_button.isVisibleTo(export_dialog.selector) is False

    export_dialog.selector.per_page_button.setChecked(True)
    assert export_dialog.mode() == "per-page"

    export_dialog.selector.chunk_button.setChecked(True)
    assert export_dialog.mode() == "chunked"
    assert export_dialog.chunk_size() == 2

    export_dialog.selector.chunk_spin.setValue(5)
    assert export_dialog.chunk_size() == 5


def test_export_dialog_warns_before_an_uneven_split(qtbot, monkeypatch):
    """Ten pages as six-page documents gives 6 + 4, so the user is asked to confirm it."""
    export_dialog = org.ExportPagesDialog("page(s) 1-10", 10)
    qtbot.addWidget(export_dialog)
    selector = export_dialog.selector
    selector.chunk_button.setChecked(True)

    # Splits that divide evenly ask nothing.
    assert selector.chunk_spin.value() == 2
    assert selector.uneven_split() is None
    selector.chunk_spin.setValue(5)
    assert selector.uneven_split() is None

    # 10 into 6 is one full document plus a 4-page leftover.
    selector.chunk_spin.setValue(6)
    assert selector.uneven_split() == (1, 4)

    asked: list[str] = []

    def refuse(*args):
        asked.append(args[2])
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(org.QMessageBox, "question", staticmethod(refuse))
    export_dialog.accept()
    assert asked[0].startswith("10 pages do not divide evenly into documents of 6 pages.")
    assert "1 document of 6 pages and 1 document of 4 pages" in asked[0]
    # Declining leaves the dialog open so the size can be corrected.
    assert export_dialog.result() != QDialog.DialogCode.Accepted

    # Confirming the warning goes ahead with the uneven split.
    monkeypatch.setattr(
        org.QMessageBox, "question", staticmethod(lambda *args: QMessageBox.StandardButton.Yes)
    )
    export_dialog.accept()
    assert export_dialog.result() == QDialog.DialogCode.Accepted


def test_export_pages_needs_confirmation_for_an_uneven_split(
    qtbot, make_pdf, tmp_path, monkeypatch
):
    _needs_qtpdf()
    widget = _open(make_pdf(pages=10), qtbot)
    monkeypatch.setattr(org, "QFileDialog", _FakeFileDialog)

    def press_export(dialog) -> int:
        """Pick "documents of 6 pages" and press Export, the way the dialog would."""
        dialog.selector.chunk_button.setChecked(True)
        dialog.selector.chunk_spin.setValue(6)
        dialog.accept()
        return int(dialog.result())

    # exec() returns the dialog's result code, so declining must look like any other cancel.
    monkeypatch.setattr(org.ExportPagesDialog, "exec", press_export)

    # Declining the warning writes nothing at all.
    monkeypatch.setattr(
        org.QMessageBox, "question", staticmethod(lambda *args: QMessageBox.StandardButton.No)
    )
    _FakeFileDialog.path = str(tmp_path / "refused")
    widget.export_pages()
    assert not (tmp_path / "refused").exists()

    # Confirming it writes the 6-page document and the 4-page remainder.
    monkeypatch.setattr(
        org.QMessageBox, "question", staticmethod(lambda *args: QMessageBox.StandardButton.Yes)
    )
    _FakeFileDialog.path = str(tmp_path / "confirmed")
    widget.export_pages()
    written = sorted((tmp_path / "confirmed").glob("*.pdf"))
    assert [path.name for path in written] == [
        "input_pages_001-006.pdf",
        "input_pages_007-010.pdf",
    ]
    assert [len(PdfReader(str(path)).pages) for path in written] == [6, 4]


def test_organizer_refuses_a_file_that_is_not_a_pdf(qtbot, tmp_path):
    """A spreadsheet is reported clearly instead of failing deep inside pypdf."""
    widget = org.PageOrganizerWidget()
    qtbot.addWidget(widget)

    spreadsheet = tmp_path / "b.xlsx"
    spreadsheet.write_bytes(b"PK\x03\x04")

    assert widget.load(str(spreadsheet)) is False
    assert widget.status.text() == f"Error: Not a PDF file: {spreadsheet.name}"
    assert widget.document is None
    assert widget.file_edit.text() == ""

    # A renamed file that is not readable as a PDF is reported the same way (one warning line
    # in the log rather than a pypdf traceback).
    fake = tmp_path / "not-really.pdf"
    fake.write_bytes(b"PK\x03\x04")
    assert widget.load(str(fake)) is False
    assert "could not be read as a PDF" in widget.status.text()


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
