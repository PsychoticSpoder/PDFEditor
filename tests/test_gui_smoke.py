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


def test_main_window_builds_with_expected_tabs(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)

    assert window.windowTitle() == "PDF Editor Tool"

    tabs = window.findChild(QTabWidget)
    assert tabs is not None
    labels = [tabs.tabText(index) for index in range(tabs.count())]
    assert labels == ["Edit", "Split", "Combine", "Convert", "Batch", "Print"]


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
