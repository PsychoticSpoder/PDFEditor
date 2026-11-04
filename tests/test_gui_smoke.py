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
