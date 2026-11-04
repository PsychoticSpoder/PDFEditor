"""PDF preview and thumbnail dialogs built on PySide6's bundled QtPdf.

QtPdf ships with PySide6, so no extra dependency is required. If it is unavailable in a
given build, the widgets degrade to a short message and the page-picker button is hidden
rather than failing.

Loading a document and rendering every page synchronously would freeze the UI on large
PDFs, so all thumbnails are produced by :class:`PdfThumbnailLoader` on a background
thread and delivered as ``QImage``s (the ``QPixmap`` is created back on the GUI thread).
This keeps the dialogs fully interactive — pages can be clicked and dragged while the
thumbnails stream in.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QSize, Qt, QThread, Signal, Slot
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

try:  # pragma: no cover - depends on the PySide6 build
    from PySide6.QtPdf import QPdfDocument

    _QT_PDF_AVAILABLE = True
except ImportError:  # pragma: no cover
    QPdfDocument = None  # type: ignore[assignment,misc]
    _QT_PDF_AVAILABLE = False

# Thumbnail render sizes
_PICKER_THUMB = QSize(110, 140)
_REORDER_THUMB = QSize(96, 128)


def qtpdf_available() -> bool:
    """True when the bundled QtPdf module can be imported."""
    return _QT_PDF_AVAILABLE


def _parse_spec_to_pages(spec: str) -> set[int]:
    """Expand a 1-based page spec like ``"1,3,5-7"`` into a set of page numbers."""
    pages: set[int] = set()
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start_text, _, end_text = token.partition("-")
            if start_text.strip().isdigit() and end_text.strip().isdigit():
                start, end = int(start_text), int(end_text)
                if start <= end:
                    pages.update(range(start, end + 1))
        elif token.isdigit():
            pages.add(int(token))
    return pages


def _format_page_selection(pages: list[int]) -> str:
    """Compress a list of 1-based page numbers into ``"1,3,5-7"`` form."""
    if not pages:
        return ""
    ordered = sorted(set(pages))
    parts: list[str] = []
    start = previous = ordered[0]
    for value in ordered[1:]:
        if value == previous + 1:
            previous = value
            continue
        parts.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = value
    parts.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(parts)


def _scaled_pixmap(image: QImage, size: QSize) -> QPixmap:
    """Turn a rendered QImage into a scaled QPixmap (GUI thread only)."""
    if image.isNull():
        return QPixmap()
    return QPixmap.fromImage(image).scaled(
        size.width(),
        size.height(),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


class _ThumbnailWorker(QObject):
    """Loads a PDF and renders every page; lives entirely on a worker thread."""

    loaded = Signal(int)
    rendered = Signal(int, QImage)
    failed = Signal(str)
    done = Signal()

    def __init__(self, path: str, size: QSize) -> None:
        super().__init__()
        self._path = path
        self._size = size
        self._stop = threading.Event()

    def cancel(self) -> None:
        self._stop.set()

    @Slot()
    def run(self) -> None:
        if not _QT_PDF_AVAILABLE:
            self.failed.emit("QtPdf is not available in this PySide6 build.")
            self.done.emit()
            return
        # Created and used only here, so the document never touches the GUI thread.
        document = QPdfDocument()
        try:
            document.load(self._path)
            page_count = document.pageCount()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            self.done.emit()
            return
        if page_count <= 0:
            self.failed.emit("Could not load the PDF for previews.")
            self.done.emit()
            return
        self.loaded.emit(page_count)
        for index in range(page_count):
            if self._stop.is_set():
                break
            image = document.render(index, self._size)
            if not image.isNull():
                self.rendered.emit(index, image)
        self.done.emit()


class PdfThumbnailLoader(QObject):
    """Owns the worker thread that renders page thumbnails off the GUI thread."""

    loaded = Signal(int)
    rendered = Signal(int, QImage)
    failed = Signal(str)

    def __init__(self, path: str, size: QSize, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread = QThread()
        self._worker = _ThumbnailWorker(path, size)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.loaded.connect(self.loaded)
        self._worker.rendered.connect(self.rendered)
        self._worker.failed.connect(self.failed)
        self._worker.done.connect(self._thread.quit)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Cancel rendering and stop the thread (safe to call more than once)."""
        self._worker.cancel()
        self._thread.quit()
        self._thread.wait(2000)


class PdfPreview(QWidget):
    """A small first-page preview with a page count."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.image = QLabel("No document")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setFixedSize(160, 200)
        self.image.setStyleSheet("border: 1px solid #dadce0; background: #f8f9fa;")
        self.info = QLabel("")
        self.info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.info.setStyleSheet("color: #5f6368;")
        layout.addWidget(self.image)
        layout.addWidget(self.info)

    def set_document(self, path: str | None) -> None:
        if not _QT_PDF_AVAILABLE or not path:
            self.image.setPixmap(QPixmap())
            self.image.setText("Preview unavailable")
            self.info.setText("")
            return
        document = QPdfDocument()
        document.load(path)
        page_count = document.pageCount()
        if page_count <= 0:
            self.image.setPixmap(QPixmap())
            self.image.setText("Could not load PDF")
            self.info.setText("")
            return
        image = document.render(0, QSize(160, 200))
        if image.isNull():
            self.image.setPixmap(QPixmap())
            self.image.setText("(no preview)")
        else:
            self.image.setPixmap(_scaled_pixmap(image, QSize(160, 200)))
        self.info.setText(f"{page_count} page(s)")


class PagePickerDialog(QDialog):
    """A thumbnail grid for choosing pages, returning a range string."""

    def __init__(
        self,
        path: str,
        preselected: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select pages")
        self.setMinimumSize(560, 480)
        self._boxes: list[QCheckBox] = []
        self._thumbnails: list[QLabel] = []
        self._preselected = _parse_spec_to_pages(preselected or "")
        self._total = 0
        self._rendered = 0
        self._loader: PdfThumbnailLoader | None = None

        layout = QVBoxLayout(self)
        self.progress = QLabel("Loading previews…")
        self.progress.setStyleSheet("color: #5f6368;")
        layout.addWidget(self.progress)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        self._grid = QGridLayout(content)
        scroll.setWidget(content)
        layout.addWidget(scroll)

        if not _QT_PDF_AVAILABLE:
            self.progress.setText("Preview unavailable; enter the page range manually.")
        else:
            self._loader = PdfThumbnailLoader(path, _PICKER_THUMB)
            self._loader.loaded.connect(self._on_loaded)
            self._loader.rendered.connect(self._on_rendered)
            self._loader.failed.connect(self._set_message)
            self._loader.start()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @Slot(int)
    def _on_loaded(self, page_count: int) -> None:
        columns = 4
        for index in range(page_count):
            cell = QWidget()
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(4, 4, 4, 4)
            thumbnail = QLabel("…")
            thumbnail.setFixedSize(_PICKER_THUMB)
            thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumbnail.setStyleSheet("border: 1px solid #dadce0; color: #9aa0a6;")
            box = QCheckBox(str(index + 1))
            box.setChecked((index + 1) in self._preselected)
            self._boxes.append(box)
            self._thumbnails.append(thumbnail)
            cell_layout.addWidget(thumbnail)
            cell_layout.addWidget(box)
            self._grid.addWidget(cell, index // columns, index % columns)
        self._total = page_count
        self._rendered = 0
        self.progress.setText(f"Rendering previews… 0/{page_count}")

    @Slot(int, QImage)
    def _on_rendered(self, index: int, image: QImage) -> None:
        if 0 <= index < len(self._thumbnails):
            self._thumbnails[index].setPixmap(_scaled_pixmap(image, _PICKER_THUMB))
        self._rendered += 1
        self.progress.setText(
            f"Rendering previews… {self._rendered}/{self._total}"
            if self._rendered < self._total
            else ""
        )

    @Slot(str)
    def _set_message(self, message: str) -> None:
        self.progress.setText(message)

    def selected_spec(self) -> str:
        return _format_page_selection(
            [index + 1 for index, box in enumerate(self._boxes) if box.isChecked()]
        )

    def closeEvent(self, event) -> None:
        if self._loader is not None:
            self._loader.stop()
        super().closeEvent(event)


class PageReorderDialog(QDialog):
    """A draggable thumbnail grid for reordering pages.

    Returns the new order as a comma-separated list of the original 1-based page numbers
    (e.g. ``"3,1,2"``), which is exactly what the Reorder operation expects.
    """

    def __init__(self, path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Reorder pages")
        self.setMinimumSize(640, 520)
        self._loader: PdfThumbnailLoader | None = None
        self._total = 0
        self._rendered = 0

        layout = QVBoxLayout(self)
        hint = QLabel("Drag the thumbnails to set the new page order.")
        hint.setStyleSheet("color: #5f6368;")
        layout.addWidget(hint)
        self.progress = QLabel("Loading previews…")
        self.progress.setStyleSheet("color: #5f6368;")
        layout.addWidget(self.progress)

        self.list = QListWidget()
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setFlow(QListView.Flow.LeftToRight)
        self.list.setWrapping(True)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setIconSize(_REORDER_THUMB)
        self.list.setGridSize(QSize(120, 176))
        self.list.setUniformItemSizes(True)
        self.list.setWordWrap(True)
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setDragEnabled(True)
        self.list.setAcceptDrops(True)
        self.list.setDropIndicatorShown(True)
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        layout.addWidget(self.list)

        controls = QHBoxLayout()
        earlier_button = QPushButton("Move Earlier")
        later_button = QPushButton("Move Later")
        earlier_button.clicked.connect(lambda: self._move_selected(-1))
        later_button.clicked.connect(lambda: self._move_selected(1))
        controls.addWidget(earlier_button)
        controls.addWidget(later_button)
        controls.addStretch(1)
        layout.addLayout(controls)

        if not _QT_PDF_AVAILABLE:
            self.progress.setText("Thumbnails unavailable; reordering needs QtPdf.")
        else:
            self._loader = PdfThumbnailLoader(path, _REORDER_THUMB)
            self._loader.loaded.connect(self._on_loaded)
            self._loader.rendered.connect(self._on_rendered)
            self._loader.failed.connect(self._set_message)
            self._loader.start()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @Slot(int)
    def _on_loaded(self, page_count: int) -> None:
        for index in range(page_count):
            item = QListWidgetItem(str(index + 1))
            item.setData(Qt.ItemDataRole.UserRole, index + 1)
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            # Pin the size so hit-testing stays correct while icons stream in.
            item.setSizeHint(QSize(120, 176))
            self.list.addItem(item)
        self._total = page_count
        self._rendered = 0
        self.progress.setText(f"Rendering previews… 0/{page_count}")

    @Slot(int, QImage)
    def _on_rendered(self, index: int, image: QImage) -> None:
        item = self.list.item(index)
        if item is not None:
            item.setIcon(QIcon(_scaled_pixmap(image, _REORDER_THUMB)))
        self._rendered += 1
        self.progress.setText(
            f"Rendering previews… {self._rendered}/{self._total}"
            if self._rendered < self._total
            else ""
        )

    @Slot(str)
    def _set_message(self, message: str) -> None:
        self.progress.setText(message)

    def _move_selected(self, offset: int) -> None:
        row = self.list.currentRow()
        target = row + offset
        if row < 0 or target < 0 or target >= self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(target, item)
        self.list.setCurrentRow(target)

    def order_spec(self) -> str:
        return ",".join(
            str(self.list.item(index).data(Qt.ItemDataRole.UserRole))
            for index in range(self.list.count())
        )

    def closeEvent(self, event) -> None:
        if self._loader is not None:
            self._loader.stop()
        super().closeEvent(event)

