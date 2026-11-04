"""Reusable PySide6 widgets and path helpers shared by the GUI panels."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import (
    _validate_existing_dir,
    _validate_existing_file,
    _validate_output_dir,
    _validate_output_path,
)



class DropLineEdit(QLineEdit):
    _BASE_STYLE = "QLineEdit { border: 1px dashed #9aa0a6; border-radius: 4px; padding: 4px; }"
    _DRAG_STYLE = (
        "QLineEdit { border: 2px dashed #1a73e8; border-radius: 4px; padding: 4px;"
        " background-color: #e8f0fe; }"
    )

    def __init__(self, allow_multiple: bool = False, directory_only: bool = False) -> None:
        super().__init__()
        self._allow_multiple = allow_multiple
        self._directory_only = directory_only
        self.setAcceptDrops(True)
        self.setStyleSheet(self._BASE_STYLE)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        mime_data = event.mimeData()
        if mime_data.hasUrls() or mime_data.hasText():
            self.setStyleSheet(self._DRAG_STYLE)
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragLeaveEvent(self, event) -> None:
        self.setStyleSheet(self._BASE_STYLE)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self.setStyleSheet(self._BASE_STYLE)
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
    line_edit.setPlaceholderText("Drop file(s) here or click the button →")
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


def _default_output_path(source_text: str, suffix: str, extension: str = ".pdf") -> str:
    """Suggest an output path beside the source: ``<stem><suffix><extension>``."""
    first = source_text.replace("\n", ";").split(";")[0].strip()
    if not first:
        return ""
    path = Path(first).expanduser()
    return str(path.with_name(f"{path.stem}{suffix}{extension}"))


def _save_row(
    label: str,
    button_text: str,
    filter_text: str = "All Files (*.*)",
    source_edit: QLineEdit | None = None,
    suffix: str = "",
    extension: str = ".pdf",
) -> tuple[QHBoxLayout, QLineEdit, QPushButton]:
    layout = QHBoxLayout()
    line_edit = DropLineEdit()
    line_edit.setToolTip("Type a path or drag a file path here.")
    line_edit.setPlaceholderText("Output path (auto-filled from the input)")
    button = QPushButton(button_text)
    layout.addWidget(QLabel(label))
    layout.addWidget(line_edit, stretch=1)
    layout.addWidget(button)

    auto_fill = {"enabled": True}

    def choose_file() -> None:
        file_name, _ = QFileDialog.getSaveFileName(None, f"Save {label}", line_edit.text(), filter_text)
        if file_name:
            auto_fill["enabled"] = False
            line_edit.setText(file_name)

    button.clicked.connect(choose_file)

    if source_edit is not None:
        def sync_default(*_args: object) -> None:
            if not auto_fill["enabled"]:
                return
            suggestion = _default_output_path(source_edit.text(), suffix, extension)
            if suggestion:
                line_edit.setText(suggestion)

        def mark_manual(*_args: object) -> None:
            auto_fill["enabled"] = False

        source_edit.textChanged.connect(sync_default)
        line_edit.textEdited.connect(mark_manual)

    return layout, line_edit, button


def _directory_row(label: str, button_text: str) -> tuple[QHBoxLayout, QLineEdit, QPushButton]:
    layout = QHBoxLayout()
    line_edit = DropLineEdit(directory_only=True)
    line_edit.setToolTip("Drag and drop a folder here.")
    line_edit.setPlaceholderText("Drop a folder here or click the button →")
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


def _validate_page_spec(spec: str, total_pages: int | None = None) -> str | None:
    """Return an error message for an invalid 1-based page spec, else None."""
    text = spec.strip()
    if not text:
        return None
    for raw_token in text.split(","):
        token = raw_token.strip()
        if not token:
            return "Empty entry in page list."
        if "-" in token:
            start_text, _, end_text = token.partition("-")
            if not (start_text.strip().isdigit() and end_text.strip().isdigit()):
                return f"Invalid range: {token}"
            start, end = int(start_text), int(end_text)
            if start < 1 or start > end:
                return f"Invalid range: {token}"
            if total_pages is not None and end > total_pages:
                return f"Page {end} exceeds {total_pages}"
        elif token.isdigit():
            value = int(token)
            if value < 1:
                return "Page numbers start at 1."
            if total_pages is not None and value > total_pages:
                return f"Page {value} exceeds {total_pages}"
        else:
            return f"Invalid page: {token}"
    return None


class PageRangeField(QWidget):
    """A page-range input with inline validation and a page-count hint."""

    def __init__(self, placeholder: str = "Optional pages, e.g. 1,3,5-7") -> None:
        super().__init__()
        self._total_pages: int | None = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.edit = DropLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.status = QLabel("")
        self.status.setStyleSheet("color: #d93025;")
        layout.addWidget(self.edit, stretch=1)
        layout.addWidget(self.status)
        self.edit.textChanged.connect(self._revalidate)

    def value(self) -> str | None:
        """Return the entered range, or None when blank (meaning "all pages")."""
        text = self.edit.text().strip()
        return text or None

    def set_page_count(self, total_pages: int) -> None:
        self._total_pages = total_pages
        self._revalidate()

    def _revalidate(self) -> None:
        error = _validate_page_spec(self.edit.text(), self._total_pages)
        if error:
            self.status.setStyleSheet("color: #d93025;")
            self.status.setText(error)
            self.edit.setStyleSheet(
                "QLineEdit { border: 1px solid #d93025; border-radius: 4px; padding: 4px; }"
            )
        else:
            self.edit.setStyleSheet(DropLineEdit._BASE_STYLE)
            if self._total_pages is not None:
                self.status.setStyleSheet("color: #5f6368;")
                self.status.setText(f"of {self._total_pages} page(s)")
            else:
                self.status.setText("")


class OrderedFileList(QWidget):
    """An ordered, reorderable list of input files (order matters for Merge)."""

    def __init__(self, filter_text: str = "All Files (*.*)") -> None:
        super().__init__()
        self._filter_text = filter_text
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setToolTip("Drag items to reorder; the order is preserved.")
        layout.addWidget(self.list)

        button_row = QHBoxLayout()
        add_button = QPushButton("Add…")
        remove_button = QPushButton("Remove")
        up_button = QPushButton("Move Up")
        down_button = QPushButton("Move Down")
        for widget in (add_button, remove_button, up_button, down_button):
            button_row.addWidget(widget)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        add_button.clicked.connect(self._add_via_dialog)
        remove_button.clicked.connect(self._remove_selected)
        up_button.clicked.connect(lambda: self._move(-1))
        down_button.clicked.connect(lambda: self._move(1))

    def add_paths(self, paths: list[str]) -> None:
        existing = {self.list.item(i).text() for i in range(self.list.count())}
        for raw in paths:
            text = str(Path(raw).expanduser().resolve())
            if text not in existing:
                self.list.addItem(text)
                existing.add(text)

    def paths(self) -> list[Path]:
        return [Path(self.list.item(i).text()) for i in range(self.list.count())]

    def _add_via_dialog(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(None, "Select files", "", self._filter_text)
        if files:
            self.add_paths(files)

    def _remove_selected(self) -> None:
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))

    def _move(self, offset: int) -> None:
        row = self.list.currentRow()
        target = row + offset
        if row < 0 or target < 0 or target >= self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(target, item)
        self.list.setCurrentRow(target)

