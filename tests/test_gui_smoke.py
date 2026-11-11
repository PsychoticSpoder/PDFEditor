"""Smoke tests for the PySide6 GUI.

These are skipped automatically when PySide6 or pytest-qt is not installed, so the
core test suite still runs in a minimal environment (e.g. the CI matrix that only
installs the PDF dependencies).
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent  # noqa: E402

from pdfeditor.gui import MainWindow  # noqa: E402
from pdfeditor.gui.widgets import _existing_file_from_input, _paths_from_input  # noqa: E402


def _url_mime(paths, owner) -> QMimeData:
    """Mime data carrying local file URLs, as an OS drag does.

    The data is parented to ``owner`` (the widget under test) because a QEvent does not own
    its mime data — without a parent the C++ object is collected before the event is used.
    """
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    mime.setParent(owner)
    return mime


def _drag_enter(paths, owner) -> QDragEnterEvent:
    """A synthetic drag-enter over the given paths."""
    return QDragEnterEvent(
        QPoint(0, 0),
        Qt.DropAction.CopyAction,
        _url_mime(paths, owner),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _drop(paths, owner) -> QDropEvent:
    """A synthetic drop of the given paths."""
    return QDropEvent(
        QPointF(0, 0),
        Qt.DropAction.CopyAction,
        _url_mime(paths, owner),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def test_main_window_builds_with_sidebar(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    assert window.windowTitle() == "PDF Editor Tool"
    assert window.panel_list.count() == window.panel_stack.count()
    assert window.panel_list.item(0).text() == "Single PDF Editing"
    assert window.panel_list.count() == 5

    # Bulk PDF Editing sits directly under Single PDF Editing; the rest keep their order.
    labels = [window.panel_list.item(row).text() for row in range(window.panel_list.count())]
    assert labels == ["Single PDF Editing", "Bulk PDF Editing", "Merge", "Convert", "Print"]


def test_sidebar_drives_panel_stack(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    window.panel_list.setCurrentRow(2)
    assert window.panel_stack.currentIndex() == 2


def test_format_and_parse_page_selection():
    from pdfeditor.gui.preview import _format_page_selection, _parse_spec_to_pages

    assert _format_page_selection([1, 2, 3, 5, 7, 8]) == "1-3,5,7-8"
    assert _format_page_selection([]) == ""
    assert _parse_spec_to_pages("1,3,5-7") == {1, 3, 5, 6, 7}
    assert _parse_spec_to_pages("") == set()


def test_open_document_loads_the_editing_panel(qtbot, make_pdf):
    src = make_pdf(pages=3)

    window = MainWindow()
    qtbot.addWidget(window)
    window.open_document(str(src))

    # Opening a PDF selects the Single PDF Editing panel and loads it there.
    assert window.panel_stack.currentIndex() == 0
    assert window.single_pdf_panel.file_edit.text() == str(src)
    assert window.single_pdf_panel.document is not None


def test_menu_bar_and_status_extras(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    titles = [action.text() for action in window.menuBar().actions()]
    assert "&File" in titles
    assert "&Help" in titles
    assert window.open_folder_button.text() == "Open folder"

    # File no longer offers Open PDF: the panel's own "PDF to edit" row is the file picker.
    labels = [action.text() for action in window.findChildren(QAction)]
    assert "&Open PDF..." not in labels
    assert "&Quit" in labels


def test_window_accepts_dropped_pdfs(qtbot, tmp_path, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    # Window-level drag-to-open: the main window accepts URL drops.
    assert window.acceptDrops() is True

    src = tmp_path / "a.pdf"
    src.write_bytes(b"%PDF")
    opened: list[str] = []
    monkeypatch.setattr(window, "open_document", opened.append)

    # Dropping a PDF opens it in the Single PDF Editing panel.
    window.dropEvent(_drop([src], window))
    assert opened == [str(src)]

    # A spreadsheet or a folder is refused: it used to be "opened" as a PDF, which switched
    # panels and reported pypdf's "Stream has ended unexpectedly".
    spreadsheet = tmp_path / "b.xlsx"
    spreadsheet.write_bytes(b"PK\x03\x04")
    folder = tmp_path / "nested"
    folder.mkdir()
    window.dropEvent(_drop([spreadsheet, folder], window))
    assert opened == [str(src)]

    # The drag is refused up front as well, so the cursor shows "no drop" and the status bar
    # explains why nothing happened.
    refused = _drag_enter([spreadsheet], window)
    window.dragEnterEvent(refused)
    assert refused.isAccepted() is False
    assert "Only PDF" in window.status.currentMessage()

    accepted = _drag_enter([src], window)
    window.dragEnterEvent(accepted)
    assert accepted.isAccepted() is True


def test_operation_panel_wires_input_output_and_run(qtbot, tmp_path):
    from PySide6.QtWidgets import QLineEdit

    from pdfeditor.gui.widgets import OperationPanel

    panel = OperationPanel("Rotate", "Rotate", source_suffix="_rotated")
    qtbot.addWidget(panel)

    calls: list[bool] = []
    panel.run_requested.connect(lambda: calls.append(True))

    # Output path auto-fills from the input beside it.
    panel.input_edit.setText(str(tmp_path / "doc.pdf"))
    assert panel.output_edit.text() == str(tmp_path / "doc_rotated.pdf")

    # add_param returns the widget so callers can keep a reference.
    extra = panel.add_param("Pages", QLineEdit())
    assert isinstance(extra, QLineEdit)

    panel.run_button.click()
    assert calls == [True]


def test_fullscreen_toggle_available(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.fullscreen_action.isCheckable()
    assert window.fullscreen_action.shortcut().toString() == "F11"


def test_existing_file_from_input_reports_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        _existing_file_from_input(str(tmp_path / "missing.pdf"), "Input PDF")


def test_paths_from_input_splits_on_semicolon(tmp_path):
    first = tmp_path / "a.pdf"
    second = tmp_path / "b.pdf"
    first.write_bytes(b"%PDF")
    second.write_bytes(b"%PDF")

    paths = _paths_from_input(f"{first};{second}")
    assert paths == [first.resolve(), second.resolve()]


def test_main_window_has_status_bar_and_cancel(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.statusBar() is not None
    assert window.cancel_button.text() == "Cancel"


def test_batch_action_drives_options_stack(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    # The combo and the stack are added in the same order (after the empty page),
    # so selecting a combo item selects the matching options page.
    window.batch_action.setCurrentIndex(0)
    window._on_batch_action_changed()
    assert window.batch_action_stacked.currentIndex() == 1

    last = window.batch_action.count() - 1
    window.batch_action.setCurrentIndex(last)
    window._on_batch_action_changed()
    assert window.batch_action_stacked.currentIndex() == last + 1


def test_batch_count_preview(qtbot, tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"%PDF")
    (tmp_path / "b.pdf").write_bytes(b"%PDF")
    (tmp_path / "c.txt").write_text("x")

    window = MainWindow()
    qtbot.addWidget(window)
    window.batch_input_dir.setText(str(tmp_path))
    window.batch_pattern.setText("*.pdf")
    assert "2 file(s)" in window.batch_count_label.text()


def test_bulk_panel_only_offers_pdf_operations(qtbot):
    """Print and the Office / PDF-to-text conversions no longer live in the bulk panel."""
    window = MainWindow()
    qtbot.addWidget(window)

    actions = [window.batch_action.itemData(row) for row in range(window.batch_action.count())]
    assert actions == ["rotate", "watermark", "delete-pages", "reorder", "split"]

    # The action is named after the Single PDF Editing panel's export.
    labels = [window.batch_action.itemText(row) for row in range(window.batch_action.count())]
    assert labels == ["Rotate", "Watermark", "Delete Pages", "Reorder", "Export Pages"]


class _NoSignal:
    """A Qt-signal stand-in: connections and emissions are both ignored."""

    def connect(self, *_slots) -> None:
        """Accept any slot."""

    def emit(self, *_args) -> None:
        """Drop any emission."""


class _IdleThread:
    """A QThread stand-in that never starts, so a batch run stays synchronous."""

    started = _NoSignal()
    finished = _NoSignal()

    def __init__(self, *_args, **_kwargs) -> None:
        """Ignore the parent window."""

    def isRunning(self) -> bool:
        return False

    def start(self) -> None:
        """Start nothing."""

    def quit(self) -> None:
        """Nothing is running, so there is nothing to stop."""


def _export_mode_buttons(selector):
    """The mode radio buttons of an :class:`ExportModeSelector`, in the offered order."""
    return [
        selector.single_button,
        selector.per_page_button,
        selector.chunk_button,
        selector.combined_button,
    ]


def test_bulk_export_pages_offers_the_single_panel_modes(qtbot):
    """Export Pages in the bulk panel offers the Single panel's modes, plus combining."""
    from pdfeditor.gui.widgets import ExportModeSelector

    window = MainWindow()
    qtbot.addWidget(window)
    window.batch_action.setCurrentIndex(window.batch_action.count() - 1)
    window._on_batch_action_changed()

    selector = window.batch_export_mode
    assert isinstance(selector, ExportModeSelector)
    # "One PDF" says "per file" here, so it cannot be mistaken for combining every PDF.
    assert [button.text() for button in _export_mode_buttons(selector)] == [
        "One PDF per file",
        "One PDF per page",
        "Documents of",
        "One PDF with every file's pages",
    ]
    assert selector.mode() == ExportModeSelector.SINGLE
    # A one-PDF-per-file export is named after each input PDF, so there is no prefix to type.
    assert window.batch_export_prefix.isEnabled() is False

    selector.per_page_button.setChecked(True)
    assert selector.mode() == ExportModeSelector.PER_PAGE
    assert window.batch_export_prefix.isEnabled() is True

    selector.chunk_button.setChecked(True)
    assert selector.mode() == ExportModeSelector.CHUNKED
    assert selector.chunk_size() == 2
    assert selector.hint.isVisibleTo(selector) is True

    selector.combined_button.setChecked(True)
    assert selector.mode() == ExportModeSelector.COMBINED
    # The combined mode uses the prefix to name its one output, and has no short final file.
    assert window.batch_export_prefix.isEnabled() is True
    assert selector.hint.isVisibleTo(selector) is False


def test_bulk_export_pages_hands_the_choice_to_the_worker(monkeypatch, tmp_path, qtbot):
    """The mode, chunk size, page range and prefix reach BatchWorker when Run is pressed."""
    import inspect

    from pdfeditor.gui import main_window as window_module
    from pdfeditor.gui.widgets import ExportModeSelector
    from pdfeditor.gui.workers import BatchWorker

    window = MainWindow()
    qtbot.addWidget(window)
    window.batch_input_dir.setText(str(tmp_path))
    window.batch_output_dir.setText(str(tmp_path / "out"))
    errors: list[Exception] = []
    monkeypatch.setattr(window, "_show_error", errors.append)

    captured: dict[str, tuple] = {}

    class _FakeWorker:
        """Capture how the window builds the worker instead of running it."""

        def __init__(self, *args, **kwargs):
            captured["call"] = (args, kwargs)
            self.progress = self.log = self.finished = self.failed = _NoSignal()

        def moveToThread(self, _thread) -> None:
            """Stay on the GUI thread; nothing is started."""

        def run(self) -> None:
            """Never reached — the fake thread never starts."""

    monkeypatch.setattr(window_module, "BatchWorker", _FakeWorker)
    monkeypatch.setattr(window_module, "QThread", _IdleThread)

    window.batch_action.setCurrentIndex(window.batch_action.count() - 1)
    window._on_batch_action_changed()
    window.batch_export_pages.setText("2,4")
    window.batch_export_prefix.setText("part")
    window.batch_export_mode.chunk_button.setChecked(True)
    window.batch_export_mode.chunk_spin.setValue(3)

    window.run_batch_operation()

    assert errors == []
    # Bind the captured arguments to BatchWorker's own parameter names.
    args, kwargs = captured["call"]
    call = inspect.signature(BatchWorker).bind(*args, **kwargs)
    assert call.arguments["action"] == "split"
    assert call.arguments["pages"] == "2,4"
    assert call.arguments["prefix"] == "part"
    assert call.arguments["export_mode"] == ExportModeSelector.CHUNKED
    assert call.arguments["chunk_size"] == 3


def test_convert_panel_offers_single_file_and_whole_folder(qtbot):
    """Word, Excel and PDF→Text each convert one file or a whole folder."""
    from pdfeditor.gui.widgets import ConversionPanel

    window = MainWindow()
    qtbot.addWidget(window)

    for panel in (window.text_conversion, window.word_conversion, window.excel_conversion):
        assert isinstance(panel, ConversionPanel)

        panel.scope.setCurrentIndex(0)  # single file
        assert panel.is_bulk() is False
        assert panel.input_edit.isVisibleTo(panel) is True
        assert panel.input_dir.isVisibleTo(panel) is False

        panel.scope.setCurrentIndex(1)  # whole folder
        assert panel.is_bulk() is True
        assert panel.input_edit.isVisibleTo(panel) is False
        assert panel.input_dir.isVisibleTo(panel) is True


def test_convert_panel_switches_panes_with_a_dropdown(qtbot):
    """One conversion at a time: the dropdown picks the pane, which then owns the panel."""
    window = MainWindow()
    qtbot.addWidget(window)

    labels = [window.convert_kind.itemText(row) for row in range(window.convert_kind.count())]
    assert labels == ["Images to PDF", "PDF to Text", "Word to PDF", "Excel to PDF"]

    # The combo and the stack are filled in the same order, so the choice is the index.
    assert window.convert_stack.count() == len(labels)
    assert window.convert_stack.currentIndex() == 0
    assert window.images_inputs.isVisibleTo(window.convert_stack) is True
    assert window.text_conversion.isVisibleTo(window.convert_stack) is False

    window.convert_kind.setCurrentIndex(2)
    assert window.convert_stack.currentWidget() is window.word_conversion
    assert window.word_conversion.isVisibleTo(window.convert_stack) is True
    assert window.images_inputs.isVisibleTo(window.convert_stack) is False

    # The image list gets the height that four stacked panes used to share.
    assert window.images_inputs.list.minimumHeight() >= 240


def test_conversion_panel_hides_the_fields_it_does_not_use(qtbot):
    """Unused fields used to float over the first "Input Folder" label."""
    from pdfeditor.gui.widgets import ConversionPanel

    # PDF to Text has workers and no continue-on-error; Word/Excel are the other way round.
    panel = ConversionPanel(
        "PDF to Text",
        input_label="Input PDF",
        input_filter="PDF Files (*.pdf)",
        pattern="*.pdf",
        button_text="Extract Text",
        with_workers=True,
    )
    qtbot.addWidget(panel)
    panel.scope.setCurrentIndex(1)
    bulk = panel._forms.widget(1)
    assert panel.workers.isVisibleTo(bulk) is True
    assert panel.continue_on_error.isVisibleTo(bulk) is False

    other = ConversionPanel(
        "Word to PDF",
        input_label="Input Word",
        input_filter="Word Files (*.docx *.doc)",
        pattern="*.doc*",
        button_text="Convert to PDF",
        with_continue_on_error=True,
    )
    qtbot.addWidget(other)
    other.scope.setCurrentIndex(1)
    other_bulk = other._forms.widget(1)
    assert other.continue_on_error.isVisibleTo(other_bulk) is True
    assert other.workers.isVisibleTo(other_bulk) is False


def test_images_pane_switches_between_one_pdf_and_one_pdf_each(qtbot):
    """The mode also swaps the output row: a filename for one PDF, a folder for one each."""
    window = MainWindow()
    qtbot.addWidget(window)

    labels = [window.images_mode.itemText(row) for row in range(window.images_mode.count())]
    # The first choice names the combined result, so it cannot be read as "one PDF each".
    assert labels == ["Combine all images into one PDF", "One PDF per image"]
    assert window.images_mode.currentData() == "single"
    assert window.images_output.isVisibleTo(window.images_outputs) is True
    assert window.images_output_dir.isVisibleTo(window.images_outputs) is False

    window.images_mode.setCurrentIndex(1)
    assert window.images_outputs.currentIndex() == 1
    assert window.images_output_dir.isVisibleTo(window.images_outputs) is True
    assert window.images_output.isVisibleTo(window.images_outputs) is False


def test_images_pane_writes_one_pdf_per_image(qtbot, tmp_path, make_image, monkeypatch):
    """Each image becomes its own PDF, named after the image."""
    from pypdf import PdfReader

    window = MainWindow()
    qtbot.addWidget(window)

    first = make_image("one.png")
    second = make_image("two.png")
    window.images_inputs.add_paths([str(first), str(second)])
    window.images_mode.setCurrentIndex(1)
    window.images_output_dir.setText(str(tmp_path / "out"))

    started: list[tuple] = []
    monkeypatch.setattr(
        window, "_run_async", lambda operation, message: started.append((operation, message))
    )
    window.run_images_to_pdf()

    operation, message = started[0]
    assert message == "Conversion complete."
    assert operation() == "Created 2 PDF(s), one per image."
    written = sorted((tmp_path / "out").glob("*.pdf"))
    assert [path.name for path in written] == ["one.pdf", "two.pdf"]
    assert [len(PdfReader(str(path)).pages) for path in written] == [1, 1]


def test_images_pane_reports_images_that_would_clash(qtbot, tmp_path, make_image, monkeypatch):
    """photo.png and photo.jpg would both write photo.pdf, so that is reported."""
    window = MainWindow()
    qtbot.addWidget(window)

    window.images_inputs.add_paths([str(make_image("photo.png")), str(make_image("photo.jpg"))])
    window.images_mode.setCurrentIndex(1)
    window.images_output_dir.setText(str(tmp_path / "out"))
    errors: list[Exception] = []
    monkeypatch.setattr(window, "_show_error", errors.append)

    window.run_images_to_pdf()

    assert len(errors) == 1
    assert "photo.pdf" in str(errors[0])


def test_bulk_word_conversion_reports_the_file_count(monkeypatch, tmp_path):
    from pdfeditor.gui import main_window as window_module

    seen: list[tuple] = []
    kwargs_seen: list[dict] = []
    written = [tmp_path / "a.pdf", tmp_path / "b.pdf"]

    def fake_batch(*args, **kwargs):
        seen.append(args)
        kwargs_seen.append(kwargs)
        return written

    monkeypatch.setattr(window_module, "batch_word_to_pdf", fake_batch)

    message = window_module.MainWindow._bulk_word_to_pdf(tmp_path, tmp_path, "*.docx", 4, True)

    # Word/Excel go through COM, which is single-threaded whatever the panel's workers say.
    assert seen == [(tmp_path, tmp_path, "*.docx", 1, None, True)]
    # The GUI asks for unique output names so an existing PDF is never clobbered.
    assert kwargs_seen == [{"unique_names": True}]
    assert message == "Converted 2 Word file(s) to PDF."


def test_excel_conversion_keeps_the_paper_size_option(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    assert window.excel_paper_size.currentText() == "A4"
    assert window.excel_conversion.isAncestorOf(window.excel_paper_size) is True


def test_validate_page_spec():
    from pdfeditor.gui.widgets import _validate_page_spec

    assert _validate_page_spec("") is None
    assert _validate_page_spec("1,3,5-7", 10) is None
    assert _validate_page_spec("0") is not None
    assert _validate_page_spec("5-1") is not None
    assert _validate_page_spec("99", 10) is not None
    assert _validate_page_spec("1,,2") is not None


def test_page_range_field_flags_out_of_range(qtbot):
    from pdfeditor.gui.widgets import PageRangeField

    field = PageRangeField()
    qtbot.addWidget(field)
    field.set_page_count(3)
    field.edit.setText("9")
    assert "exceeds 3" in field.status.text()
    field.edit.setText("2")
    assert "of 3 page(s)" in field.status.text()


def test_ordered_file_list_paths_and_reorder(qtbot, tmp_path):
    from pdfeditor.gui.widgets import OrderedFileList

    first = tmp_path / "a.pdf"
    second = tmp_path / "b.pdf"
    first.write_bytes(b"%PDF")
    second.write_bytes(b"%PDF")

    widget = OrderedFileList("PDF Files (*.pdf)")
    qtbot.addWidget(widget)
    widget.add_paths([str(first), str(second)])
    assert widget.paths() == [first.resolve(), second.resolve()]

    # Move the first item down one position.
    widget.list.setCurrentRow(0)
    widget._move(1)
    assert widget.paths() == [second.resolve(), first.resolve()]

    # Duplicates are ignored.
    widget.add_paths([str(first)])
    assert len(widget.paths()) == 2

    # The list accepts files dropped from the OS.
    assert widget.list.acceptDrops() is True


def test_ordered_file_list_refuses_folders_and_other_file_types(qtbot, tmp_path):
    """A folder or a spreadsheet dropped on Merge's list is refused, not queued for merging."""
    from pdfeditor.gui.widgets import OrderedFileList

    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF")
    spreadsheet = tmp_path / "b.xlsx"
    spreadsheet.write_bytes(b"PK\x03\x04")
    folder = tmp_path / "nested"
    folder.mkdir()

    widget = OrderedFileList("PDF Files (*.pdf)")
    qtbot.addWidget(widget)
    widget.add_paths([str(spreadsheet), str(pdf), str(folder)])

    assert widget.paths() == [pdf.resolve()]
    assert widget.accepts(str(pdf)) is True
    assert widget.accepts(str(spreadsheet)) is False
    assert widget.accepts(str(folder)) is False
    # The refusal is explained rather than looking like the drop did nothing.
    assert widget.status.text() == "Ignored b.xlsx, nested: *.pdf only."

    # A drop the list can use clears the complaint.
    extra = tmp_path / "c.pdf"
    extra.write_bytes(b"%PDF")
    widget.add_paths([str(extra)])
    assert widget.paths() == [pdf.resolve(), extra.resolve()]
    assert widget.status.text() == ""


def test_ordered_file_list_takes_its_types_from_its_filter(qtbot, tmp_path):
    """The Images→PDF list keeps taking images — and now refuses a PDF."""
    from pdfeditor.gui.widgets import OrderedFileList

    image = tmp_path / "scan.png"
    image.write_bytes(b"\x89PNG")
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF")

    widget = OrderedFileList("Images (*.png *.jpg)")
    qtbot.addWidget(widget)
    assert widget.accepts_description == "*.jpg or *.png"
    widget.add_paths([str(image), str(pdf)])
    assert widget.paths() == [image.resolve()]


def test_ordered_file_list_filters_a_dropped_mixture(qtbot, tmp_path):
    """The drop path itself — the one that used to queue a spreadsheet for merging."""
    from pdfeditor.gui.widgets import OrderedFileList

    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF")
    spreadsheet = tmp_path / "b.xlsx"
    spreadsheet.write_bytes(b"PK\x03\x04")

    widget = OrderedFileList("PDF Files (*.pdf)")
    qtbot.addWidget(widget)

    widget.list.dropEvent(_drop([spreadsheet, pdf], widget))

    assert widget.paths() == [pdf.resolve()]
    assert "b.xlsx" in widget.status.text()


def test_pdf_path_field_refuses_a_dropped_spreadsheet(qtbot, tmp_path):
    """A PDF-filtered path field takes the right type and refuses the rest."""
    from pdfeditor.gui.widgets import DropLineEdit

    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF")
    spreadsheet = tmp_path / "b.xlsx"
    spreadsheet.write_bytes(b"PK\x03\x04")

    field = DropLineEdit(filter_text="PDF Files (*.pdf)")
    qtbot.addWidget(field)
    assert field.accepts(str(spreadsheet)) is False
    assert field.accepts(str(pdf)) is True

    # The drag is refused up front, so the cursor shows "no drop" ...
    refused = _drag_enter([spreadsheet], field)
    field.dragEnterEvent(refused)
    assert refused.isAccepted() is False

    # ... and a drop that arrives anyway leaves the field empty.
    field.dropEvent(_drop([spreadsheet], field))
    assert field.text() == ""

    accepted = _drag_enter([pdf], field)
    field.dragEnterEvent(accepted)
    assert accepted.isAccepted() is True
    field.dropEvent(_drop([pdf], field))
    assert field.text() == str(pdf.resolve())


def test_pdf_to_edit_field_only_takes_pdfs(qtbot, tmp_path):
    """The reported case: an xlsx dropped on "PDF to edit" never reaches the organizer."""
    window = MainWindow()
    qtbot.addWidget(window)
    file_edit = window.single_pdf_panel.file_edit

    spreadsheet = tmp_path / "b.xlsx"
    spreadsheet.write_bytes(b"PK\x03\x04")
    assert file_edit.accepts(str(spreadsheet)) is False

    file_edit.dropEvent(_drop([spreadsheet], file_edit))

    assert file_edit.text() == ""
    assert window.single_pdf_panel.document is None


def test_merge_auto_fills_the_output_from_the_first_input(qtbot, tmp_path):
    """Merge's "auto-filled from the input" output row works for a list of inputs too."""
    from pathlib import Path

    window = MainWindow()
    qtbot.addWidget(window)

    first = tmp_path / "a.pdf"
    second = tmp_path / "b.pdf"
    third = tmp_path / "c.pdf"
    for path in (first, second, third):
        path.write_bytes(b"%PDF")

    window.combine_inputs.add_paths([str(first), str(second)])
    assert Path(window.combine_output.text()) == tmp_path / "a_combined.pdf"

    # A path the user put there themselves is never overwritten by a later add.
    window.combine_output.setText(str(tmp_path / "mine.pdf"))
    window.combine_inputs.add_paths([str(third)])
    assert Path(window.combine_output.text()) == tmp_path / "mine.pdf"

    # Clearing the field starts the suggestions again — here from the new first input.
    window.combine_output.clear()
    window.combine_inputs.list.setCurrentRow(0)
    window.combine_inputs._move(1)
    assert Path(window.combine_output.text()) == tmp_path / "b_combined.pdf"


def test_merge_refuses_a_spreadsheet_that_reached_the_list(qtbot, tmp_path, monkeypatch):
    """Belt and braces: something that is not a PDF never reaches pypdf."""
    window = MainWindow()
    qtbot.addWidget(window)
    errors: list[Exception] = []
    monkeypatch.setattr(window, "_show_error", errors.append)

    spreadsheet = tmp_path / "b.xlsx"
    spreadsheet.write_bytes(b"PK\x03\x04")
    window.combine_inputs.list.addItem(str(spreadsheet))  # bypasses the drop filter
    window.combine_output.setText(str(tmp_path / "out.pdf"))

    window.run_combine()

    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)
    assert "Not a PDF file: b.xlsx" in str(errors[0])


def test_merge_keeps_an_existing_output_and_writes_a_unique_name(qtbot, tmp_path, monkeypatch):
    """An existing output is never clobbered: the merge lands beside it as ``out (1).pdf``."""
    from pdfeditor.gui import main_window as window_module

    window = MainWindow()
    qtbot.addWidget(window)

    source = tmp_path / "a.pdf"
    source.write_bytes(b"%PDF")
    existing = tmp_path / "out.pdf"
    existing.write_bytes(b"%PDF-original")
    window.combine_inputs.add_paths([str(source)])
    window.combine_output.setText(str(existing))

    written: list = []
    monkeypatch.setattr(
        window_module, "combine_pdfs", lambda inputs, output: written.append(output)
    )
    monkeypatch.setattr(window, "_run_async", lambda operation, message: operation())

    window.run_combine()

    assert [path.name for path in written] == ["out (1).pdf"]
    assert existing.read_bytes() == b"%PDF-original"


def test_merge_uses_the_requested_name_when_it_is_free(qtbot, tmp_path, monkeypatch):
    """With no clash the requested filename is used unchanged."""
    from pdfeditor.gui import main_window as window_module

    window = MainWindow()
    qtbot.addWidget(window)

    source = tmp_path / "a.pdf"
    source.write_bytes(b"%PDF")
    window.combine_inputs.add_paths([str(source)])
    window.combine_output.setText(str(tmp_path / "out.pdf"))

    written: list = []
    monkeypatch.setattr(
        window_module, "combine_pdfs", lambda inputs, output: written.append(output)
    )
    monkeypatch.setattr(window, "_run_async", lambda operation, message: operation())

    window.run_combine()

    assert [path.name for path in written] == ["out.pdf"]


def test_print_panel_lists_input_pdfs_in_order(qtbot, tmp_path):
    """The Print panel uses Merge's ordered list, so the print order can be arranged."""
    from pdfeditor.gui.widgets import OrderedFileList

    window = MainWindow()
    qtbot.addWidget(window)
    assert isinstance(window.print_inputs, OrderedFileList)

    first = tmp_path / "a.pdf"
    second = tmp_path / "b.pdf"
    first.write_bytes(b"%PDF")
    second.write_bytes(b"%PDF")
    window.print_inputs.add_paths([str(first), str(second)])
    assert window.print_inputs.paths() == [first.resolve(), second.resolve()]

    # Reordering the list changes the order the PDFs are printed in.
    window.print_inputs.list.setCurrentRow(0)
    window.print_inputs._move(1)
    assert window.print_inputs.paths() == [second.resolve(), first.resolve()]


def test_print_in_order_sends_every_pdf_in_list_order(monkeypatch, tmp_path):
    from pdfeditor.gui import main_window as window_module

    printed: list[str] = []
    monkeypatch.setattr(window_module, "print_pdf", lambda path: printed.append(path.name))

    window_module._print_in_order([tmp_path / "a.pdf", tmp_path / "b.pdf"])

    assert printed == ["a.pdf", "b.pdf"]


def test_page_reorder_dialog_order_spec(qtbot, make_pdf):
    from pdfeditor.gui.preview import PageReorderDialog, qtpdf_available

    if not qtpdf_available():
        import pytest as _pytest

        _pytest.skip("QtPdf not available")

    src = make_pdf(pages=3)
    dialog = PageReorderDialog(str(src))
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog.list.count() == 3, timeout=5000)
    assert dialog.order_spec() == "1,2,3"

    # Move the last page to the front using the same primitive the drag uses.
    assert dialog.list.move_row_to(2, 0) is True
    assert dialog.order_spec() == "3,1,2"


def test_page_reorder_dialog_items_are_laid_out(qtbot, make_pdf):
    from pdfeditor.gui.preview import PageReorderDialog, qtpdf_available

    if not qtpdf_available():
        import pytest as _pytest

        _pytest.skip("QtPdf not available")

    src = make_pdf(pages=4)
    dialog = PageReorderDialog(str(src))
    qtbot.addWidget(dialog)
    dialog.resize(760, 600)
    dialog.show()
    qtbot.waitUntil(lambda: dialog.list.count() == 4, timeout=5000)
    qtbot.wait(80)

    viewport = dialog.list.viewport().rect()

    def item_positions() -> list[tuple[int, int, int, int]]:
        return [
            (
                dialog.list.visualItemRect(dialog.list.item(i)).x(),
                dialog.list.visualItemRect(dialog.list.item(i)).y(),
                dialog.list.visualItemRect(dialog.list.item(i)).width(),
                dialog.list.visualItemRect(dialog.list.item(i)).height(),
            )
            for i in range(4)
        ]

    before = item_positions()
    assert all(rect[2] > 0 and rect[3] > 0 for rect in before)
    assert len({(rect[0], rect[1]) for rect in before}) == 4
    assert all(viewport.contains(dialog.list.visualItemRect(dialog.list.item(i))) for i in range(4))

    # Thumbnails arrive from the worker thread; layout/hit-testing must stay correct.
    qtbot.waitUntil(lambda: not dialog.list.item(0).icon().isNull(), timeout=5000)
    after = item_positions()
    assert all(rect[2] > 0 and rect[3] > 0 for rect in after)
    assert len({(rect[0], rect[1]) for rect in after}) == 4
    assert all(viewport.contains(dialog.list.visualItemRect(dialog.list.item(i))) for i in range(4))


def test_page_reorder_dialog_move_buttons(qtbot, make_pdf):
    from pdfeditor.gui.preview import PageReorderDialog, qtpdf_available

    if not qtpdf_available():
        import pytest as _pytest

        _pytest.skip("QtPdf not available")

    src = make_pdf(pages=3)
    dialog = PageReorderDialog(str(src))
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog.list.count() == 3, timeout=5000)
    dialog.list.setCurrentRow(0)
    dialog._move_selected(1)
    assert dialog.order_spec() == "2,1,3"


def test_page_reorder_dialog_drag_reorders(qtbot, make_pdf):
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from pdfeditor.gui.preview import PageReorderDialog, qtpdf_available

    if not qtpdf_available():
        pytest.skip("QtPdf not available")

    src = make_pdf(pages=3)
    dialog = PageReorderDialog(str(src))
    qtbot.addWidget(dialog)
    dialog.resize(700, 560)
    dialog.show()
    qtbot.waitUntil(lambda: dialog.list.count() == 3, timeout=5000)

    lst = dialog.list
    start = lst.visualItemRect(lst.item(0)).center()
    end = lst.visualItemRect(lst.item(2)).center()

    def send(kind, pos, buttons):
        event = QMouseEvent(
            kind,
            QPointF(pos),
            QPointF(pos),
            Qt.MouseButton.LeftButton,
            buttons,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(lst.viewport(), event)

    send(QEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton)
    send(QEvent.Type.MouseMove, start + QPoint(0, 25), Qt.MouseButton.LeftButton)
    send(QEvent.Type.MouseMove, end, Qt.MouseButton.LeftButton)
    send(QEvent.Type.MouseButtonRelease, end, Qt.MouseButton.NoButton)

    # Dragging page 1 down onto page 3 reorders the list to 2,3,1.
    assert dialog.order_spec() == "2,3,1"


def test_page_picker_dialog_selected_spec(qtbot, make_pdf):
    from pdfeditor.gui.preview import PagePickerDialog, qtpdf_available

    if not qtpdf_available():
        import pytest as _pytest

        _pytest.skip("QtPdf not available")

    src = make_pdf(pages=3)
    dialog = PagePickerDialog(str(src), "1,3")
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: len(dialog._boxes) == 3, timeout=5000)
    assert dialog.selected_spec() == "1,3"


def test_page_reorder_dialog_grid_toggle(qtbot, make_pdf):
    from pdfeditor.gui.preview import PageReorderDialog, qtpdf_available

    if not qtpdf_available():
        pytest.skip("QtPdf not available")

    src = make_pdf(pages=3)
    dialog = PageReorderDialog(str(src))
    qtbot.addWidget(dialog)
    qtbot.waitUntil(lambda: dialog.list.count() == 3, timeout=5000)

    # Grid by default; the toggle switches to a list without losing the items.
    assert dialog.list.is_grid() is True
    dialog.grid_toggle.setChecked(False)
    assert dialog.list.is_grid() is False
    assert dialog.order_spec() == "1,2,3"
    dialog.grid_toggle.setChecked(True)
    assert dialog.list.is_grid() is True


def test_single_pdf_panel_embeds_the_organizer(qtbot):
    from pdfeditor.gui.organizer import PageOrganizerWidget

    window = MainWindow()
    qtbot.addWidget(window)

    assert window.panel_list.item(0).text() == "Single PDF Editing"
    assert isinstance(window.single_pdf_panel, PageOrganizerWidget)
    # The one panel replaces the eight per-operation panels, so their widgets are gone.
    for removed in (
        "rotate_input",
        "watermark_input",
        "delete_input",
        "reorder_input",
        "extract_input",
        "insert_input",
        "meta_input",
        "split_input",
    ):
        assert not hasattr(window, removed)


def test_the_shared_current_pdf_bar_is_gone(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    # The panel's own "PDF to edit" row replaced the Current PDF bar, its Open PDF button,
    # the recent-documents dropdown and the first-page preview.
    for removed in ("document_bar", "current_document", "recent_combo", "preview"):
        assert not hasattr(window, removed)
    assert window.single_pdf_panel.file_edit.placeholderText().startswith("Drop or choose")
    assert window.panel_list.currentRow() == 0


def test_batch_dry_run_lists_files(qtbot, tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"%PDF")
    (tmp_path / "b.pdf").write_bytes(b"%PDF")

    window = MainWindow()
    qtbot.addWidget(window)
    window.batch_input_dir.setText(str(tmp_path))
    window.batch_pattern.setText("*.pdf")
    window.run_batch_dry_run()
    log = window.batch_log.toPlainText()
    assert "a.pdf" in log and "b.pdf" in log
    assert "dry-run" in log


def test_default_output_path_suggests_sibling(tmp_path):
    from pdfeditor.gui.widgets import _default_output_path

    source = str(tmp_path / "report.pdf")
    assert _default_output_path(source, "_rotated") == str(tmp_path / "report_rotated.pdf")


def test_save_row_auto_fills_output(tmp_path):
    from pdfeditor.gui.widgets import _file_row, _save_row

    _in_row, in_edit, _btn = _file_row("Input", "Browse")
    _out_row, out_edit, _btn2 = _save_row(
        "Output", "Save As", source_edit=in_edit, suffix="_rotated"
    )
    in_edit.setText(str(tmp_path / "report.pdf"))
    assert out_edit.text() == str(tmp_path / "report_rotated.pdf")
