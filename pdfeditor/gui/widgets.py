"""Reusable PySide6 widgets and path helpers shared by the GUI panels."""

from __future__ import annotations

import re
from functools import partial
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QIcon, QImage, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ..core import (
    DEFAULT_WORKERS,
    EXPORT_CHUNKED,
    EXPORT_COMBINED,
    EXPORT_PER_PAGE,
    EXPORT_SINGLE,
    MAX_WORKERS,
    MIN_WORKERS,
    _validate_existing_dir,
    _validate_existing_file,
    _validate_output_dir,
    _validate_output_path,
)

# Thumbnail sizes shared by every page grid (picker, reorder dialog, organizer).
GRID_ICON_SIZE = QSize(96, 128)
GRID_CELL_SIZE = QSize(122, 172)
LIST_ICON_SIZE = QSize(64, 84)
LIST_ROW_HEIGHT = 92

#: Item-data role holding the QPixmap that :class:`_ThumbnailDelegate` paints.
THUMBNAIL_ROLE = int(Qt.ItemDataRole.UserRole) + 1
#: Item-data role holding a page's width/height ratio, kept even without a thumbnail.
ASPECT_ROLE = int(Qt.ItemDataRole.UserRole) + 2
_CAPTION_HEIGHT = 18
_CELL_PADDING = 6
#: Widest/tallest thumbnail a grid cell allows; cells are this tall whatever the page.
_GRID_THUMB_BOX = QSize(160, 142)
#: Zoom window for the grid thumbnails (1.0 = the default :data:`_GRID_THUMB_BOX` size).
MIN_THUMB_SCALE = 1.0
MAX_THUMB_SCALE = 5.0


def _scaled(size: QSize, scale: float) -> QSize:
    """``size`` multiplied by ``scale``, each dimension rounded to a whole pixel."""
    return QSize(round(size.width() * scale), round(size.height() * scale))


class _ThumbnailDelegate(QStyledItemDelegate):
    """Paints a page thumbnail at its own aspect ratio inside its grid cell.

    ``QListWidget``'s stock delegate draws every icon inside one global icon box, which
    squeezes a landscape page into a portrait box. Here each cell is sized from the page
    itself and the pixmap is centred in whatever room the cell provides, so a rotated or
    landscape page gets a landscape thumbnail.
    """

    def __init__(self, parent: QWidget | None = None, caption_below: bool = True) -> None:
        super().__init__(parent)
        self._caption_below = caption_below

    def set_caption_below(self, caption_below: bool) -> None:
        """Grid cells caption under the thumbnail; list rows caption beside it."""
        self._caption_below = caption_below

    @staticmethod
    def _fit(pixmap: QPixmap, box: QSize) -> QPixmap:
        """Down-scale ``pixmap`` to ``box`` if needed (never up-scales, so it stays crisp)."""
        if pixmap.width() <= box.width() and pixmap.height() <= box.height():
            return pixmap
        return pixmap.scaled(
            box.width(),
            box.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    @staticmethod
    def _centred(pixmap: QPixmap, area: QRect) -> QRect:
        """The rect ``pixmap`` occupies when centred inside ``area``."""
        return QRect(
            area.left() + (area.width() - pixmap.width()) // 2,
            area.top() + (area.height() - pixmap.height()) // 2,
            pixmap.width(),
            pixmap.height(),
        )

    def paint(self, painter, option, index) -> None:
        widget = option.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, option, painter, widget)

        rect = option.rect
        caption = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        pixmap = index.data(THUMBNAIL_ROLE)
        if not isinstance(pixmap, QPixmap) or pixmap.isNull():
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, caption)
            return

        if self._caption_below:
            area = QRect(
                rect.left(), rect.top(), rect.width(), max(1, rect.height() - _CAPTION_HEIGHT)
            )
            target = self._centred(self._fit(pixmap, area.size()), area)
            caption_rect = QRect(
                rect.left(),
                rect.bottom() - _CAPTION_HEIGHT + 1,
                rect.width(),
                _CAPTION_HEIGHT,
            )
            caption_flags = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        else:
            area = QRect(
                rect.left() + _CELL_PADDING,
                rect.top() + _CELL_PADDING,
                max(1, rect.width() - 2 * _CELL_PADDING),
                max(1, rect.height() - 2 * _CELL_PADDING),
            )
            fitted = self._fit(pixmap, area.size())
            target = QRect(
                area.left(),
                area.top() + (area.height() - fitted.height()) // 2,
                fitted.width(),
                fitted.height(),
            )
            text_left = target.right() + _CELL_PADDING
            caption_rect = QRect(
                text_left, rect.top(), max(0, rect.right() - text_left), rect.height()
            )
            caption_flags = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft

        painter.drawPixmap(target.topLeft(), self._fit(pixmap, target.size()))
        if option.state & QStyle.StateFlag.State_Selected:
            # An outline makes the selection obvious; the panel highlight alone is subtle.
            painter.save()
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(option.palette.highlight().color(), 2))
            painter.drawRect(target.adjusted(-1, -1, 1, 1))
            painter.restore()
        painter.drawText(caption_rect, caption_flags, caption)


class DropLineEdit(QLineEdit):
    _BASE_STYLE = "QLineEdit { border: 1px dashed #9aa0a6; border-radius: 4px; padding: 4px; }"
    _DRAG_STYLE = (
        "QLineEdit { border: 2px dashed #1a73e8; border-radius: 4px; padding: 4px;"
        " background-color: #e8f0fe; }"
    )

    def __init__(
        self,
        allow_multiple: bool = False,
        directory_only: bool = False,
        filter_text: str = "",
    ) -> None:
        """A path field that accepts dropped files.

        ``filter_text`` (a Qt name filter such as ``"PDF Files (*.pdf)"``) limits which drops are
        taken, so a spreadsheet dropped on a PDF field is refused instead of being handed to the
        operation behind it — which is how "Stream has ended unexpectedly" used to happen. An
        empty or extension-less filter (``"All Files (*.*)"``) takes any file.
        """
        super().__init__()
        self._allow_multiple = allow_multiple
        self._directory_only = directory_only
        self._allowed_suffixes = _suffixes_from_filter(filter_text)
        self.setAcceptDrops(True)
        self.setStyleSheet(self._BASE_STYLE)

    def _dropped_paths(self, event) -> list[str]:
        """The local paths of a drag/drop event, in the order they were dropped."""
        return [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]

    def accepts(self, raw: str) -> bool:
        """True when this field can use the dropped path (right type, and it exists)."""
        path = Path(raw)
        if self._directory_only:
            # A dropped file is taken as "the folder it lives in".
            return path.is_dir() or path.is_file()
        return path.is_file() and (
            not self._allowed_suffixes or path.suffix.lower() in self._allowed_suffixes
        )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """Highlight for a usable drop; refuse the rest with the "no drop" cursor."""
        mime_data = event.mimeData()
        if mime_data.hasUrls():
            if not any(self.accepts(path) for path in self._dropped_paths(event)):
                event.ignore()
                return
        elif not mime_data.hasText():
            super().dragEnterEvent(event)
            return
        self.setStyleSheet(self._DRAG_STYLE)
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self.setStyleSheet(self._BASE_STYLE)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self.setStyleSheet(self._BASE_STYLE)
        mime_data = event.mimeData()
        if mime_data.hasUrls():
            paths = [
                str(Path(raw).expanduser().resolve())
                for raw in self._dropped_paths(event)
                if self.accepts(raw)
            ]
            if not paths:
                event.ignore()
                return

            if self._directory_only:
                directory_paths = [path for path in paths if Path(path).is_dir()]
                if directory_paths:
                    self.setText(directory_paths[0])
                else:
                    self.setText(str(Path(paths[0]).parent))
            elif self._allow_multiple:
                self.setText(";".join(paths))
            else:
                self.setText(paths[0])

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
    line_edit = DropLineEdit(allow_multiple=pick_many, filter_text=filter_text)
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
        file_name, _ = QFileDialog.getSaveFileName(
            None, f"Save {label}", line_edit.text(), filter_text
        )
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


def _link_default_output(
    save_edit: QLineEdit,
    sources: OrderedFileList,
    suffix: str,
    extension: str = ".pdf",
) -> None:
    """Keep a save row's default output in step with an ordered list of inputs.

    The suggestion is ``<first input stem><suffix>`` beside the first input — the same shape
    :func:`_save_row` gives a single-input panel, which several inputs otherwise cannot use
    because they have no one "source" field. Once the field holds a path the user put there
    themselves (typed, or picked with *Save As…*), it is left alone; clearing it starts the
    suggestions again.
    """

    suggested = {"text": ""}

    def sync_default(*_args: object) -> None:
        current = save_edit.text().strip()
        if current and current != suggested["text"]:
            return
        paths = sources.paths()
        if not paths:
            return
        suggestion = _default_output_path(str(paths[0]), suffix, extension)
        if suggestion:
            suggested["text"] = suggestion
            save_edit.setText(suggestion)

    sources.paths_changed.connect(sync_default)


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


def _row_widget(row: QLayout) -> QWidget:
    """Wrap a ready-made row (``_file_row``/``_save_row``/``_directory_row``) in a widget.

    A stack of rows needs widgets, not layouts: this is what lets one row swap for another.
    """
    holder = QWidget()
    row.setContentsMargins(0, 0, 0, 0)
    holder.setLayout(row)
    return holder


def _paths_from_input(raw_text: str) -> list[Path]:
    normalized = raw_text.replace("\n", ";")
    return [
        Path(part.strip()).expanduser().resolve() for part in normalized.split(";") if part.strip()
    ]


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
        self.pick_button = QPushButton("Pick…")
        self.pick_button.setToolTip("Choose pages from a preview")
        self.pick_button.setVisible(False)
        self.pick_button.clicked.connect(self._pick_pages)
        layout.addWidget(self.pick_button)
        self._document: str | None = None
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

    def set_document(self, path: str | None) -> None:
        """Remember the PDF to pick pages from (shows the picker button if possible)."""
        self._document = path
        from .preview import qtpdf_available

        self.pick_button.setVisible(bool(path) and qtpdf_available())

    def _pick_pages(self) -> None:
        if not self._document:
            return
        from .preview import PagePickerDialog

        dialog = PagePickerDialog(self._document, self.edit.text().strip(), self)
        if dialog.exec():
            spec = dialog.selected_spec()
            if spec:
                self.edit.setText(spec)


def _suffixes_from_filter(filter_text: str) -> frozenset[str]:
    """The extensions a Qt name filter allows: ``"Images (*.png *.jpg)"`` → ``.png``/``.jpg``.

    A filter without extensions (``"All Files (*.*)"``) yields an empty set, which
    :class:`OrderedFileList` reads as "any file goes".
    """
    return frozenset(match.lower() for match in re.findall(r"\*(\.[0-9A-Za-z]+)", filter_text))


class _DropListWidget(QListWidget):
    """A reorderable list that also accepts files dropped from the OS."""

    def __init__(self, owner: OrderedFileList) -> None:
        super().__init__()
        self._owner = owner
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setAcceptDrops(True)

    def _dropped_paths(self, event) -> list[str]:
        """The local paths of a drag/drop event (folders included, so they can be refused)."""
        return [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]

    def _accepts_drop(self, event) -> bool:
        """True when an external drop brings at least one file the list can use."""
        return any(self._owner.accepts(raw) for raw in self._dropped_paths(event))

    def dragEnterEvent(self, event) -> None:
        if event.source() is self:
            super().dragEnterEvent(event)
        elif self._accepts_drop(event):
            event.acceptProposedAction()
        else:
            # Refusing here rather than on drop is what shows the "no drop" cursor.
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.source() is self:
            super().dragMoveEvent(event)
        elif self._accepts_drop(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        if event.source() is self:
            super().dropEvent(event)
        elif event.mimeData().hasUrls():
            # add_paths takes what it can use and reports the rest.
            self._owner.add_paths(self._dropped_paths(event))
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class OrderedFileList(QWidget):
    """An ordered, reorderable list of input files (order matters for Merge).

    Only files matching the list's filter can be added, so a folder or a spreadsheet dropped
    on the list is refused — and reported under the buttons — instead of reaching the
    operation behind it and failing halfway through. The accepted extensions come from
    ``filter_text`` unless ``allowed_suffixes`` is given explicitly.
    """

    #: Emitted when the list's contents or order change (e.g. to update a default output).
    paths_changed = Signal()

    def __init__(
        self,
        filter_text: str = "All Files (*.*)",
        allowed_suffixes: tuple[str, ...] | None = None,
    ) -> None:
        super().__init__()
        self._filter_text = filter_text
        self._allowed_suffixes = (
            frozenset(allowed_suffixes)
            if allowed_suffixes is not None
            else _suffixes_from_filter(filter_text)
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.list = _DropListWidget(self)
        self.list.setToolTip("Drag items to reorder, or drop files here to add them.")
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

        # Refused drops are explained here rather than looking like nothing happened.
        self.status = QLabel("")
        self.status.setStyleSheet("color: #d93025;")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        add_button.clicked.connect(self._add_via_dialog)
        remove_button.clicked.connect(self._remove_selected)
        up_button.clicked.connect(lambda: self._move(-1))
        down_button.clicked.connect(lambda: self._move(1))

    @property
    def accepts_description(self) -> str:
        """What this list takes, e.g. ``"*.pdf"`` — used when reporting a refused path."""
        if not self._allowed_suffixes:
            return "any file"
        return " or ".join(f"*{suffix}" for suffix in sorted(self._allowed_suffixes))

    def accepts(self, raw: str) -> bool:
        """True when ``raw`` is a file this list can use (right type, not a folder)."""
        path = Path(raw)
        return path.is_file() and (
            not self._allowed_suffixes or path.suffix.lower() in self._allowed_suffixes
        )

    def add_paths(self, paths: list[str]) -> None:
        """Add the usable paths in order, and report the ones this list cannot take."""
        existing = {self.list.item(i).text() for i in range(self.list.count())}
        added = False
        refused: list[str] = []
        for raw in paths:
            if not self.accepts(raw):
                refused.append(str(Path(raw).expanduser().resolve()))
                continue
            text = str(Path(raw).expanduser().resolve())
            if text not in existing:
                self.list.addItem(text)
                existing.add(text)
                added = True
        if added:
            self.paths_changed.emit()
        self._report_refused(refused)

    def paths(self) -> list[Path]:
        return [Path(self.list.item(i).text()) for i in range(self.list.count())]

    def _report_refused(self, refused: list[str]) -> None:
        """Say what was skipped, so a drop never looks like it silently did nothing."""
        if not refused:
            self.status.clear()
            return
        names = ", ".join(Path(path).name for path in refused[:3])
        if len(refused) > 3:
            names += f" and {len(refused) - 3} more"
        self.status.setText(f"Ignored {names}: {self.accepts_description} only.")

    def _add_via_dialog(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(None, "Select files", "", self._filter_text)
        if files:
            self.add_paths(files)

    def _remove_selected(self) -> None:
        selected = self.list.selectedItems()
        for item in selected:
            self.list.takeItem(self.list.row(item))
        if selected:
            self.paths_changed.emit()

    def _move(self, offset: int) -> None:
        row = self.list.currentRow()
        target = row + offset
        if row < 0 or target < 0 or target >= self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(target, item)
        self.list.setCurrentRow(target)
        self.paths_changed.emit()


class ConversionPanel(QGroupBox):
    """A conversion that runs on one file, or on a whole folder of them.

    The "single file" form is the usual file-in / file-out pair. The "whole folder" form
    mirrors the Bulk PDF Editing panel (input folder + glob pattern + output folder), so a
    single box covers both the one-off conversion and the bulk run. Only the form for the
    chosen scope is shown, and callers can add settings that both scopes share.
    """

    run_requested = Signal()

    def __init__(
        self,
        title: str,
        *,
        input_label: str,
        input_filter: str,
        pattern: str,
        button_text: str,
        output_label: str = "Output PDF",
        output_filter: str = "PDF Files (*.pdf)",
        output_extension: str = ".pdf",
        output_suffix: str = "",
        with_workers: bool = False,
        with_continue_on_error: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(title, parent)

        layout = QVBoxLayout(self)
        self._options = QFormLayout()
        layout.addLayout(self._options)

        self.scope = QComboBox()
        self.scope.addItem("Single file", False)
        self.scope.addItem("Whole folder (bulk)", True)
        self.scope.currentIndexChanged.connect(self._apply_scope)
        self._options.addRow("Run on", self.scope)

        # -- one file: pick it, then say where its result goes --
        single = QWidget(self)
        single_form = QFormLayout(single)
        single_form.setContentsMargins(0, 0, 0, 0)
        self.input_row, self.input_edit, _ = _file_row(
            input_label, "Browse", filter_text=input_filter
        )
        self.output_row, self.output_edit, _ = _save_row(
            output_label,
            "Save As",
            filter_text=output_filter,
            source_edit=self.input_edit,
            suffix=output_suffix,
            extension=output_extension,
        )
        single_form.addRow(self.input_row)
        single_form.addRow(self.output_row)

        # -- a whole folder: the same fields the Bulk PDF Editing panel uses --
        bulk = QWidget(self)
        bulk_form = QFormLayout(bulk)
        bulk_form.setContentsMargins(0, 0, 0, 0)
        self.bulk_input_row, self.input_dir, _ = _directory_row("Input Folder", "Select Folder")
        self.bulk_output_row, self.output_dir, _ = _directory_row("Output Folder", "Select Folder")
        self.pattern = QLineEdit(pattern, bulk)
        self.pattern.setToolTip("Glob pattern, supports forms like *.pdf or **/*.pdf")
        self.workers = QSpinBox(bulk)
        self.workers.setMinimum(MIN_WORKERS)
        self.workers.setMaximum(MAX_WORKERS)
        self.workers.setValue(DEFAULT_WORKERS)
        self.continue_on_error = QCheckBox("Continue on error", bulk)
        self.continue_on_error.setChecked(True)
        bulk_form.addRow(self.bulk_input_row)
        bulk_form.addRow(self.bulk_output_row)
        bulk_form.addRow("Pattern", self.pattern)
        if with_workers:
            bulk_form.addRow("Workers", self.workers)
        else:
            # A widget that never reaches a layout would float over the first row's label, in
            # the page's top-left corner, so the ones this panel does not use are hidden.
            self.workers.hide()
        if with_continue_on_error:
            bulk_form.addRow(self.continue_on_error)
        else:
            self.continue_on_error.hide()

        self._forms = QStackedWidget()
        self._forms.addWidget(single)
        self._forms.addWidget(bulk)
        layout.addWidget(self._forms)

        self.button = QPushButton(button_text)
        self.button.clicked.connect(self.run_requested)
        layout.addWidget(self.button)

    def is_bulk(self) -> bool:
        """True when the user asked for a whole-folder conversion."""
        return bool(self.scope.currentData())

    def add_option(self, label: str, widget: QWidget) -> None:
        """Add a setting that applies to both scopes, e.g. Excel's paper size."""
        self._options.addRow(label, widget)

    def _apply_scope(self) -> None:
        """Show the form for the chosen scope."""
        self._forms.setCurrentIndex(1 if self.is_bulk() else 0)


class OperationPanel(QGroupBox):
    """A reusable file-in / file-out operation panel.

    Lays out an input row, an auto-filled output row, any parameter rows, and a primary
    run button. The window connects :attr:`run_requested` to the operation's handler, so
    every file-to-file operation shares the same structure and behaviour.
    """

    run_requested = Signal()

    def __init__(
        self,
        title: str,
        run_button_text: str,
        input_label: str = "Input PDF",
        output_label: str = "Output PDF",
        filter_text: str = "PDF Files (*.pdf)",
        source_suffix: str = "",
        output_extension: str = ".pdf",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(title, parent)
        self.form = QFormLayout(self)

        self.input_row, self.input_edit, _ = _file_row(
            input_label, "Browse", filter_text=filter_text
        )
        self.output_row, self.output_edit, _ = _save_row(
            output_label,
            "Save As",
            filter_text=filter_text,
            source_edit=self.input_edit,
            suffix=source_suffix,
            extension=output_extension,
        )
        self.form.addRow(self.input_row)
        self.form.addRow(self.output_row)

        self.run_button = QPushButton(run_button_text)
        self.run_button.clicked.connect(self.run_requested)
        self.form.addRow(self.run_button)

    def add_param(self, label: str, widget: QWidget) -> QWidget:
        """Insert a parameter row above the run button and return the widget."""
        self.form.insertRow(self.form.rowCount() - 1, label, widget)
        return widget

    def add_input_row(self, label: str, filter_text: str = "All Files (*.*)") -> QLineEdit:
        """Insert an extra file-input row above the run button; return its line edit."""
        row, line_edit, _ = _file_row(label, "Browse", filter_text=filter_text)
        self.form.insertRow(self.form.rowCount() - 1, row)
        return line_edit

    def input_path(self) -> str:
        return self.input_edit.text()

    def output_path(self) -> str:
        return self.output_edit.text()


# Bounds and default for the "Documents of N pages each" spin box.
MIN_EXPORT_CHUNK_SIZE = 2
MAX_EXPORT_CHUNK_SIZE = 999
DEFAULT_EXPORT_CHUNK_SIZE = 2


class ExportModeSelector(QWidget):
    """The ways an export can split pages, shared by both "Export Pages" panels.

    "One PDF per file" (just "One PDF" for a single document) keeps each document's pages
    together, "One PDF per page" writes a file per page, and "Documents of N pages each" writes
    one file per group of N pages. ``with_combined`` adds "One PDF with every file's pages",
    which merges the chosen pages of every matching PDF into a single file; that only makes
    sense for a whole-folder export, so the single-document dialog leaves it out.

    ``page_count`` is how many pages the export covers when that is known — it drives the
    uneven-split check in :meth:`uneven_split`; leave it at ``0`` for whole-folder work, where
    every input file has its own page count.
    """

    #: Mode ids returned by :meth:`mode`, matching the core's ``EXPORT_*`` constants.
    SINGLE = EXPORT_SINGLE
    PER_PAGE = EXPORT_PER_PAGE
    CHUNKED = EXPORT_CHUNKED
    COMBINED = EXPORT_COMBINED

    #: Emitted with the new :meth:`mode` whenever the choice changes.
    mode_changed = Signal(str)

    def __init__(
        self, page_count: int = 0, with_combined: bool = False, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.page_count = page_count
        self._mode = self.SINGLE

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Once a combining option is on offer, "One PDF" has to say what it makes one PDF *of*.
        self.single_button = QRadioButton("One PDF per file" if with_combined else "One PDF")
        self.single_button.setChecked(True)
        self.per_page_button = QRadioButton("One PDF per page")

        # e.g. "Documents of [3] pages each" — one PDF per group of N pages.
        self.chunk_button = QRadioButton("Documents of")
        self.chunk_spin = QSpinBox()
        self.chunk_spin.setRange(MIN_EXPORT_CHUNK_SIZE, MAX_EXPORT_CHUNK_SIZE)
        self.chunk_spin.setValue(DEFAULT_EXPORT_CHUNK_SIZE)
        self.chunk_spin.setSuffix(" pages each")
        self.chunk_spin.setEnabled(False)
        self.chunk_button.toggled.connect(self.chunk_spin.setEnabled)

        chunk_row = QHBoxLayout()
        chunk_row.setContentsMargins(0, 0, 0, 0)
        chunk_row.addWidget(self.chunk_button)
        chunk_row.addWidget(self.chunk_spin)
        chunk_row.addStretch(1)

        # A short final document is easy to miss, so say so while the choice is on screen.
        self.hint = QLabel("Leftover pages go into one shorter final document.")
        self.hint.setStyleSheet("color: #5f6368;")
        self.hint.setContentsMargins(24, 0, 0, 0)
        self.hint.setVisible(False)

        # Only a whole-folder export can gather several documents into one PDF.
        self.combined_button = QRadioButton("One PDF with every file's pages")
        self.combined_button.setVisible(with_combined)

        layout.addWidget(self.single_button)
        layout.addWidget(self.per_page_button)
        layout.addLayout(chunk_row)
        layout.addWidget(self.hint)
        layout.addWidget(self.combined_button)

        for button, mode in (
            (self.single_button, self.SINGLE),
            (self.per_page_button, self.PER_PAGE),
            (self.chunk_button, self.CHUNKED),
            (self.combined_button, self.COMBINED),
        ):
            button.toggled.connect(partial(self._on_mode_toggled, mode))

    def mode(self) -> str:
        """The chosen export: :data:`SINGLE`, :data:`PER_PAGE`, :data:`CHUNKED` or
        :data:`COMBINED`."""
        if self.combined_button.isChecked():
            return self.COMBINED
        if self.chunk_button.isChecked():
            return self.CHUNKED
        return self.SINGLE if self.single_button.isChecked() else self.PER_PAGE

    def chunk_size(self) -> int:
        """How many pages go into each document in :data:`CHUNKED` mode."""
        return self.chunk_spin.value()

    def uneven_split(self) -> tuple[int, int] | None:
        """The ``(full_files, remainder)`` the choice would leave, or ``None`` when it is even.

        ``None`` also covers the modes that never leave a short final document, and a page
        count the selector was not told about.
        """
        if self.mode() != self.CHUNKED or self.page_count <= 0:
            return None
        full, remainder = divmod(self.page_count, self.chunk_size())
        return (full, remainder) if remainder else None

    def _on_mode_toggled(self, mode: str, checked: bool) -> None:
        """Track the choice as it settles — Qt also reports the outgoing button."""
        if not checked or mode == self._mode:
            return
        self._mode = mode
        self.hint.setVisible(mode == self.CHUNKED)
        self.mode_changed.emit(mode)


class PageGridWidget(QListWidget):
    """A list/grid of page thumbnails whose items are reordered by dragging.

    Reordering is driven directly from mouse events rather than Qt's drag-and-drop
    machinery, so it works reliably regardless of platform/theme: while the left button
    is held, the item under the pointer is moved to follow it. With ``multi_select=True``
    a whole selection travels as one block, so a group of pages can be dropped somewhere
    else together.
    """

    rows_moved = Signal()

    def __init__(self, grid: bool = True, multi_select: bool = False) -> None:
        super().__init__()
        self._grid = grid
        self._multi_select = multi_select
        self._thumb_scale = MIN_THUMB_SCALE
        self._item_size = QSize(0, LIST_ROW_HEIGHT)
        self._drag_rows: list[int] = []
        self._drag_moved = False
        self._press_pos = None
        # Cells follow each page's own aspect ratio, so they are not uniform.
        self.setUniformItemSizes(False)
        self.setItemDelegate(_ThumbnailDelegate(self, caption_below=grid))
        self.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
            if multi_select
            else QAbstractItemView.SelectionMode.SingleSelection
        )
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self._apply_mode()

    # -- layout ---------------------------------------------------------------
    def _apply_mode(self) -> None:
        if self._grid:
            self.setViewMode(QListView.ViewMode.IconMode)
            self.setFlow(QListView.Flow.LeftToRight)
            self.setWrapping(True)
            self.setResizeMode(QListView.ResizeMode.Adjust)
            self.setIconSize(_scaled(GRID_ICON_SIZE, self._thumb_scale))
            self._item_size = self._grid_item_size()
        else:
            self.setViewMode(QListView.ViewMode.ListMode)
            self.setFlow(QListView.Flow.TopToBottom)
            self.setWrapping(False)
            self.setIconSize(LIST_ICON_SIZE)
            self._item_size = QSize(0, LIST_ROW_HEIGHT)
        self.setMovement(QListView.Movement.Static)
        delegate = self.itemDelegate()
        if isinstance(delegate, _ThumbnailDelegate):
            delegate.set_caption_below(self._grid)
        for row in range(self.count()):
            self._resize_item(self.item(row))

    def _apply_scale(self) -> None:
        """Apply the current zoom to the sizes without resetting the view mode.

        Zooming only changes the icon and cell sizes, so this deliberately skips the heavier
        :meth:`_apply_mode` view-mode calls to keep dragging the zoom slider smooth.
        """
        if self._grid:
            self.setIconSize(_scaled(GRID_ICON_SIZE, self._thumb_scale))
            self._item_size = self._grid_item_size()
        for row in range(self.count()):
            self._resize_item(self.item(row))
        # Recompute the flow positions once, deterministically. Relying on the implicit
        # relayout that individual size-hint changes trigger left QListView drawing cells at
        # their previous offsets, so zoomed-in pages overlapped each other.
        self.relayout()

    def _grid_item_size(self) -> QSize:
        """The placeholder size of a grid cell at the current zoom."""
        return QSize(
            round(GRID_CELL_SIZE.width() * self._thumb_scale),
            self.cell_height(self._thumb_scale),
        )

    def is_grid(self) -> bool:
        return self._grid

    def set_grid(self, grid: bool) -> None:
        """Switch between the icon grid and the single-column list."""
        if grid != self._grid:
            self._grid = grid
            self._apply_mode()

    def item_size(self) -> QSize:
        """The size hint every item should use in the current mode."""
        return QSize(self._item_size)

    def thumbnail_scale(self) -> float:
        """The current grid-thumbnail zoom factor (1.0 = the default size)."""
        return self._thumb_scale

    def set_thumbnail_scale(self, scale: float) -> None:
        """Zoom the grid thumbnails, clamped to the supported ``MIN``/``MAX`` range.

        The cells are resized at once; the caller is expected to re-render the pages
        afterwards so a zoomed-in thumbnail is drawn at the new, higher resolution.
        """
        scale = min(max(scale, MIN_THUMB_SCALE), MAX_THUMB_SCALE)
        if scale == self._thumb_scale:
            return
        self._thumb_scale = scale
        self._apply_scale()

    def thumbnail_box(self) -> QSize:
        """The largest thumbnail that fits one cell in the current mode."""
        return self._grid_thumb_box() if self._grid else QSize(LIST_ICON_SIZE)

    def _grid_thumb_box(self) -> QSize:
        """The grid thumbnail bounding box, grown by the current zoom factor."""
        return _scaled(_GRID_THUMB_BOX, self._thumb_scale)

    def set_thumbnail(self, item: QListWidgetItem, image: QImage) -> None:
        """Show ``image`` on ``item`` and shape its cell like the page itself.

        The cell is sized from the thumbnail, so a landscape (or rotated) page gets a
        landscape cell instead of a portrait one with squashed content.
        """
        if image.isNull():
            return
        pixmap = QPixmap.fromImage(image)
        item.setData(THUMBNAIL_ROLE, pixmap)
        # The stock icon is set too, so callers can still query item.icon().
        item.setIcon(QIcon(pixmap))
        # Remember the page's shape, so its cell keeps its size even after the pixmap is
        # dropped again (lazy rendering) or re-rendered at another resolution.
        item.setData(ASPECT_ROLE, pixmap.width() / pixmap.height())
        # Only rewrite the cell size when it actually changed. A page re-rendered at a new
        # resolution keeps its aspect ratio, so the hint is unchanged and QListView is spared
        # a full relayout per streamed image (which caused the lag and the overlaps).
        size = self._cell_size_for(pixmap.size())
        if item.sizeHint() != size:
            item.setSizeHint(size)

    def clear_thumbnail(self, item: QListWidgetItem) -> None:
        """Drop an item's pixmap to free its memory, keeping the cell the same size.

        Lazy rendering calls this for pages that have scrolled far out of view. The aspect
        ratio stored alongside the thumbnail still sizes the cell, so nothing reflows and the
        page simply shows its caption until it is scrolled back into view and rendered again.
        """
        if item.data(THUMBNAIL_ROLE) is None and item.icon().isNull():
            return
        item.setData(THUMBNAIL_ROLE, None)
        item.setIcon(QIcon())

    def has_thumbnail(self, item: QListWidgetItem) -> bool:
        """True while the item is actually holding a thumbnail pixmap."""
        pixmap = item.data(THUMBNAIL_ROLE)
        return isinstance(pixmap, QPixmap) and not pixmap.isNull()

    def _cell_size_for(self, thumbnail: QSize) -> QSize:
        """A cell for a thumbnail, sized from the page's aspect ratio in grid mode."""
        if self._grid:
            aspect = thumbnail.width() / thumbnail.height() if thumbnail.height() else 0.0
            return self.cell_size_for_aspect(aspect)
        return QSize(
            0, max(LIST_ROW_HEIGHT, thumbnail.height() + _CAPTION_HEIGHT + 2 * _CELL_PADDING)
        )

    def cell_size_for_aspect(self, aspect: float) -> QSize:
        """The grid cell for a page of the given width/height ``aspect`` at the current zoom.

        Fitting the aspect ratio into the zoom box — instead of using the rendered pixmap's
        pixel width — keeps a cell's size hint stable when the same page is re-rendered at a
        different resolution, and lets the cell keep its size even after the pixmap is dropped
        to save memory. The constant height keeps every "Page N" caption on one baseline, while
        a landscape page still gets a wider cell than a portrait one.
        """
        box = self._grid_thumb_box()
        width = box.width()
        if aspect > 0:
            width = min(box.width(), max(1, round(box.height() * aspect)))
        return QSize(width + 2 * _CELL_PADDING, self.cell_height(self._thumb_scale))

    @staticmethod
    def cell_height(scale: float = 1.0) -> int:
        """The fixed height of a grid cell (thumbnail area + caption + padding)."""
        return round(_GRID_THUMB_BOX.height() * scale) + _CAPTION_HEIGHT + 2 * _CELL_PADDING

    def _resize_item(self, item: QListWidgetItem) -> None:
        """(Re)apply an item's cell size, e.g. after switching grid/list mode."""
        pixmap = item.data(THUMBNAIL_ROLE)
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            size = self._cell_size_for(pixmap.size())
        elif self._grid:
            aspect = item.data(ASPECT_ROLE)
            size = (
                self.cell_size_for_aspect(float(aspect))
                if isinstance(aspect, float) and aspect > 0
                else QSize(self._item_size)
            )
        else:
            size = QSize(self._item_size)
        # Skipping a no-op hint assignment keeps a zoom from relayouting the whole grid for
        # every cell whose size did not really change.
        if item.sizeHint() != size:
            item.setSizeHint(size)

    # -- ordering -------------------------------------------------------------
    def selected_rows(self) -> list[int]:
        """Rows of the selected items, in document order."""
        return sorted(self.row(item) for item in self.selectedItems())

    def relayout(self) -> None:
        """Recompute the flow positions from the items' current sizes.

        ``QListView`` caches where it last drew each cell and only recomputes when it decides
        the layout is dirty, which leaves cells drawn at their *previous* offsets after a move
        or a size change. Asking for the layout explicitly is what fixes that; it costs a few
        hundred microseconds for a few hundred cells, so it is safe to run per drag step.
        """
        self.doItemsLayout()

    def move_rows_to(self, rows: list[int], target: int) -> bool:
        """Move the items at ``rows`` so the block lands at index ``target``.

        ``target`` indexes the list as it is *now*, which is what a pointer hit-test
        produces; rows inside the moved block are ignored. Returns True when the order
        actually changed.
        """
        count = self.count()
        rows = sorted({row for row in rows if 0 <= row < count})
        if not rows or not 0 <= target < count or target in rows:
            return False
        moving = [self.item(row) for row in rows]
        for row in reversed(rows):
            self.takeItem(row)
        insert_at = min(target, self.count())
        for offset, item in enumerate(moving):
            self.insertItem(insert_at + offset, item)
        # Keep the moved block selected so a continuing drag still tracks it. The current
        # item is set first because it resets the selection on its own.
        self.setCurrentItem(moving[0])
        for offset in range(1, len(moving)):
            self.item(insert_at + offset).setSelected(True)
        # A moved item keeps its own cell width, so the cached positions no longer describe
        # the model: without an explicit relayout the cells stay at the offsets of the items
        # they traded places with, pushing the last cell of a row past the right edge until
        # something else (a window resize) forced a real relayout.
        self.relayout()
        return True

    def move_row_to(self, row: int, target: int) -> bool:
        """Move a single item (used by the Move Earlier/Later buttons)."""
        return self.move_rows_to([row], target)

    # -- drag interaction -----------------------------------------------------
    def _target_index(self, pos) -> int | None:
        """Index of the item under ``pos`` (or the nearest centre) for a drop."""
        count = self.count()
        if count == 0:
            return None
        item = self.itemAt(pos)
        if item is not None:
            return self.row(item)
        best_row: int | None = None
        best_distance: int | None = None
        for row in range(count):
            rect = self.visualItemRect(self.item(row))
            if not rect.isValid():
                continue
            centre = rect.center()
            distance = (centre.x() - pos.x()) ** 2 + (centre.y() - pos.y()) ** 2
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_row = row
        return best_row

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
            item = self.itemAt(self._press_pos)
            if item is None:
                self._drag_rows = []
            elif item.isSelected():
                # Pressing inside the selection keeps the whole block for the drag.
                self._drag_rows = self.selected_rows()
            else:
                self._drag_rows = [self.row(item)]
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (
            not self._drag_rows
            or self._press_pos is None
            or not (event.buttons() & Qt.MouseButton.LeftButton)
        ):
            super().mouseMoveEvent(event)
            return
        pos = event.position().toPoint()
        if (pos - self._press_pos).manhattanLength() < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return
        target = self._target_index(pos)
        if target is not None and target not in self._drag_rows:
            if self.move_rows_to(self._drag_rows, target):
                self._drag_rows = self.selected_rows()
                self._drag_moved = True
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        moved = self._drag_moved
        self._drag_moved = False
        self._drag_rows = []
        self._press_pos = None
        super().mouseReleaseEvent(event)
        if moved:
            # Emitted once per finished drag, not on every mouse move along the way.
            self.rows_moved.emit()
