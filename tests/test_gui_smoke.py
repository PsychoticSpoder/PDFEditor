"""Smoke tests for the PySide6 GUI.

These are skipped automatically when PySide6 or pytest-qt is not installed, so the
core test suite still runs in a minimal environment (e.g. the CI matrix that only
installs the PDF dependencies).
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QTabWidget  # noqa: E402

from pdfeditor.gui import MainWindow  # noqa: E402
from pdfeditor.gui.widgets import _existing_file_from_input, _paths_from_input  # noqa: E402


def test_main_window_builds_with_sidebar(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    assert window.windowTitle() == "PDF Editor Tool"
    assert window.panel_list.count() == window.panel_stack.count()
    assert window.panel_list.item(0).text() == "Rotate"
    assert window.panel_list.count() >= 12


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


def test_current_document_propagates_to_panels(qtbot, tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF")

    window = MainWindow()
    qtbot.addWidget(window)
    window.current_document.setText(str(src))

    assert window.rotate_input.text() == str(src)
    assert window.split_input.text() == str(src)
    assert window.print_input.text() == str(src)


def test_menu_bar_and_status_extras(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    titles = [action.text() for action in window.menuBar().actions()]
    assert "&File" in titles
    assert "&Help" in titles
    assert window.open_folder_button.text() == "Open folder"


def test_window_accepts_dropped_pdfs(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    # Window-level drag-to-open: the main window accepts URL drops.
    assert window.acceptDrops() is True


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
    assert all(
        viewport.contains(dialog.list.visualItemRect(dialog.list.item(i))) for i in range(4)
    )

    # Thumbnails arrive from the worker thread; layout/hit-testing must stay correct.
    qtbot.waitUntil(lambda: not dialog.list.item(0).icon().isNull(), timeout=5000)
    after = item_positions()
    assert all(rect[2] > 0 and rect[3] > 0 for rect in after)
    assert len({(rect[0], rect[1]) for rect in after}) == 4
    assert all(
        viewport.contains(dialog.list.visualItemRect(dialog.list.item(i))) for i in range(4)
    )


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


def test_reorder_pick_button_present(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.reorder_pick_button.text().startswith("Reorder")


def test_edit_tab_has_parity_controls(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.extract_pages is not None
    assert window.insert_pages_field is not None
    assert window.meta_title is not None


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
