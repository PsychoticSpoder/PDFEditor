"""Reusable PySide6 widgets and path helpers shared by the GUI panels."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
)

from ..core import (
    _validate_existing_dir,
    _validate_existing_file,
    _validate_output_dir,
    _validate_output_path,
)



class DropLineEdit(QLineEdit):
    def __init__(self, allow_multiple: bool = False, directory_only: bool = False) -> None:
        super().__init__()
        self._allow_multiple = allow_multiple
        self._directory_only = directory_only
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        mime_data = event.mimeData()
        if mime_data.hasUrls() or mime_data.hasText():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        mime_data = event.mimeData()
        if mime_data.hasUrls():
            paths = [Path(url.toLocalFile()).expanduser().resolve() for url in mime_data.urls() if url.isLocalFile()]
            if not paths:
                return

            if self._directory_only:
                directory_paths = [path for path in paths if path.is_dir()]
                if directory_paths:
                    self.setText(str(directory_paths[0]))
                else:
                    self.setText(str(paths[0].parent))
            elif self._allow_multiple:
                self.setText(";".join(str(path) for path in paths))
            else:
                self.setText(str(paths[0]))

            event.acceptProposedAction()
            return

        if mime_data.hasText():
            self.setText(mime_data.text().strip())
            event.acceptProposedAction()
            return

        super().dropEvent(event)


def _file_row(
    label: str,
    button_text: str,
    pick_many: bool = False,
    filter_text: str = "All Files (*.*)",
) -> tuple[QHBoxLayout, QLineEdit, QPushButton]:
    layout = QHBoxLayout()
    line_edit = DropLineEdit(allow_multiple=pick_many)
    line_edit.setToolTip("Drag and drop files here.")
    button = QPushButton(button_text)
    layout.addWidget(QLabel(label))
    layout.addWidget(line_edit, stretch=1)
    layout.addWidget(button)

    def choose_files() -> None:
        if pick_many:
            files, _ = QFileDialog.getOpenFileNames(None, f"Select {label}", "", filter_text)
            if files:
                line_edit.setText(";".join(files))
        else:
            file_name, _ = QFileDialog.getOpenFileName(None, f"Select {label}", "", filter_text)
            if file_name:
                line_edit.setText(file_name)

    button.clicked.connect(choose_files)
    return layout, line_edit, button


def _save_row(
    label: str,
    button_text: str,
    filter_text: str = "All Files (*.*)",
) -> tuple[QHBoxLayout, QLineEdit, QPushButton]:
    layout = QHBoxLayout()
    line_edit = DropLineEdit()
    line_edit.setToolTip("Type a path or drag a file path here.")
    button = QPushButton(button_text)
    layout.addWidget(QLabel(label))
    layout.addWidget(line_edit, stretch=1)
    layout.addWidget(button)

    def choose_file() -> None:
        file_name, _ = QFileDialog.getSaveFileName(None, f"Save {label}", "", filter_text)
        if file_name:
            line_edit.setText(file_name)

    button.clicked.connect(choose_file)
    return layout, line_edit, button


def _directory_row(label: str, button_text: str) -> tuple[QHBoxLayout, QLineEdit, QPushButton]:
    layout = QHBoxLayout()
    line_edit = DropLineEdit(directory_only=True)
    line_edit.setToolTip("Drag and drop a folder here.")
    button = QPushButton(button_text)
    layout.addWidget(QLabel(label))
    layout.addWidget(line_edit, stretch=1)
    layout.addWidget(button)

    def choose_directory() -> None:
        directory = QFileDialog.getExistingDirectory(None, f"Select {label}")
        if directory:
            line_edit.setText(directory)

    button.clicked.connect(choose_directory)
    return layout, line_edit, button


def _paths_from_input(raw_text: str) -> list[Path]:
    normalized = raw_text.replace("\n", ";")
    return [Path(part.strip()).expanduser().resolve() for part in normalized.split(";") if part.strip()]


def _existing_file_from_input(raw_text: str, label: str) -> Path:
    text = raw_text.strip()
    if not text:
        raise ValueError(f"{label} is required.")
    try:
        return _validate_existing_file(text)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{label} not found: {Path(text).expanduser().resolve()}") from exc


def _output_file_from_input(raw_text: str, label: str) -> Path:
    text = raw_text.strip()
    if not text:
        raise ValueError(f"{label} is required.")
    return _validate_output_path(text)


def _output_dir_from_input(raw_text: str, label: str) -> Path:
    text = raw_text.strip()
    if not text:
        raise ValueError(f"{label} is required.")
    return _validate_output_dir(text)


def _existing_dir_from_input(raw_text: str, label: str) -> Path:
    text = raw_text.strip()
    if not text:
        raise ValueError(f"{label} is required.")
    try:
        return _validate_existing_dir(text)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{label} not found: {Path(text).expanduser().resolve()}") from exc

