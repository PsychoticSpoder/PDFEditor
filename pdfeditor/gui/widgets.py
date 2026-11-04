"""Reusable PySide6 widgets and path helpers shared by the GUI panels."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QIcon, QImage, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ..core import (
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
_CAPTION_HEIGHT = 18
_CELL_PADDING = 6
#: Widest/tallest thumbnail a grid cell allows; cells are this tall whatever the page.
_GRID_THUMB_BOX = QSize(160, 142)


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


class _DropListWidget(QListWidget):
    """A reorderable list that also accepts files dropped from the OS."""

    def __init__(self, owner: "OrderedFileList") -> None:
        super().__init__()
        self._owner = owner
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setAcceptDrops(True)

    def _is_external(self, event) -> bool:
        return event.source() is not self and event.mimeData().hasUrls()

    def dragEnterEvent(self, event) -> None:
        if event.source() is self or self._is_external(event):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.source() is self or self._is_external(event):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if event.source() is self:
            super().dropEvent(event)
        elif self._is_external(event):
            self._owner.add_paths(
                [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
            )
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class OrderedFileList(QWidget):
    """An ordered, reorderable list of input files (order matters for Merge)."""

    def __init__(self, filter_text: str = "All Files (*.*)") -> None:
        super().__init__()
        self._filter_text = filter_text
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
            self.setIconSize(GRID_ICON_SIZE)
            self._item_size = QSize(GRID_CELL_SIZE)
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

    def thumbnail_box(self) -> QSize:
        """The largest thumbnail that fits one cell in the current mode."""
        return QSize(_GRID_THUMB_BOX if self._grid else LIST_ICON_SIZE)

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
        item.setSizeHint(self._cell_size_for(pixmap.size()))

    def _cell_size_for(self, thumbnail: QSize) -> QSize:
        """A cell sized from the thumbnail's width but always the *same* height.

        The constant height keeps every "Page N" caption on one baseline, while a landscape
        page still gets a wider cell than a portrait one.
        """
        if self._grid:
            width = min(thumbnail.width(), _GRID_THUMB_BOX.width())
            return QSize(width + 2 * _CELL_PADDING, self.cell_height())
        return QSize(
            0, max(LIST_ROW_HEIGHT, thumbnail.height() + _CAPTION_HEIGHT + 2 * _CELL_PADDING)
        )

    @staticmethod
    def cell_height() -> int:
        """The fixed height of a grid cell (thumbnail area + caption + padding)."""
        return _GRID_THUMB_BOX.height() + _CAPTION_HEIGHT + 2 * _CELL_PADDING

    def _resize_item(self, item: QListWidgetItem) -> None:
        """(Re)apply an item's cell size, e.g. after switching grid/list mode."""
        pixmap = item.data(THUMBNAIL_ROLE)
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            item.setSizeHint(self._cell_size_for(pixmap.size()))
        else:
            item.setSizeHint(self._item_size)

    # -- ordering -------------------------------------------------------------
    def selected_rows(self) -> list[int]:
        """Rows of the selected items, in document order."""
        return sorted(self.row(item) for item in self.selectedItems())

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
