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
