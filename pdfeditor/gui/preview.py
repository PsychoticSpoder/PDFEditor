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

from PySide6.QtCore import QObject, QSize, QSizeF, Qt, QThread, Signal, Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .widgets import PageGridWidget

try:  # pragma: no cover - depends on the PySide6 build
    from PySide6.QtPdf import QPdfDocument

    _QT_PDF_AVAILABLE = True
except ImportError:  # pragma: no cover
    QPdfDocument = None  # type: ignore[assignment,misc]
    _QT_PDF_AVAILABLE = False

# Thumbnail render sizes
_PICKER_THUMB = QSize(110, 140)


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
    """Loads a PDF and renders the requested pages; lives on a worker thread.

    ``QPdfDocument.render()`` stretches a page into whatever size it is handed, so each
    page is rendered at its *own* aspect ratio (from :meth:`QPdfDocument.pagePointSize`,
    which already accounts for page rotation). That keeps landscape and rotated pages from
    being squashed into a portrait thumbnail.
    """

    loaded = Signal(int)
    rendered = Signal(int, QImage)
    failed = Signal(str)
    done = Signal()

    def __init__(self, path: str, size: QSize, pages: list[int] | None = None) -> None:
        super().__init__()
        self._path = path
        self._size = size
        self._pages = None if pages is None else list(pages)
        self._stop = threading.Event()

    def cancel(self) -> None:
        self._stop.set()

    def _render_size(self, document, index: int) -> QSize:
        """The largest image of the page's own shape that fits in the requested box."""
        point_size = document.pagePointSize(index)
        if point_size.isEmpty():
            return self._size
        fitted = QSizeF(point_size).scaled(QSizeF(self._size), Qt.AspectRatioMode.KeepAspectRatio)
        return QSize(max(1, round(fitted.width())), max(1, round(fitted.height())))

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
        targets = range(page_count) if self._pages is None else self._pages
        for index in targets:
            if self._stop.is_set():
                break
            if not 0 <= index < page_count:
                continue
            image = document.render(index, self._render_size(document, index))
            if not image.isNull():
                self.rendered.emit(index, image)
        self.done.emit()


#: Loaders whose worker thread has not stopped yet. Qt treats destroying a running QThread
#: as fatal, so a loader keeps itself alive here until its thread really finishes — letting a
#: render outlive the widget that asked for it (e.g. a panel being torn down mid-render).
_ACTIVE_LOADERS: set[PdfThumbnailLoader] = set()


class PdfThumbnailLoader(QObject):
    """Owns the worker thread that renders page thumbnails off the GUI thread.

    ``pages`` restricts rendering to those 0-based page indexes (all pages when omitted),
    which lets a caller show a document it has already partly rendered and only refresh
    the pages that actually changed. ``rendered`` reports the page index, not the position
    within the request, so the caller can map images back to its own rows.
    """

    loaded = Signal(int)
    rendered = Signal(int, QImage)
    failed = Signal(str)

    def __init__(
        self,
        path: str,
        size: QSize,
        pages: list[int] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._thread = QThread()
        self._worker = _ThumbnailWorker(path, size, pages)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.loaded.connect(self.loaded)
        self._worker.rendered.connect(self.rendered)
        self._worker.failed.connect(self.failed)
        self._worker.done.connect(self._thread.quit)
        self._thread.finished.connect(self._retire)
        _ACTIVE_LOADERS.add(self)

    def _retire(self) -> None:
        """Drop the keep-alive reference once the worker thread has really stopped."""
        _ACTIVE_LOADERS.discard(self)

    def start(self) -> None:
        self._thread.start()

    def cancel(self) -> None:
        """Ask the worker to stop without waiting for its thread.

        The GUI thread never blocks here: the worker notices the request between pages, its
        thread quits on its own, and :data:`_ACTIVE_LOADERS` keeps the loader alive until the
        thread has really stopped — so it can never be destroyed while still running.
        """
        self._worker.cancel()
        self._thread.quit()

    def stop(self) -> None:
        """Cancel rendering and stop the thread (safe to call more than once)."""
        self.cancel()
        self._thread.wait(2000)


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
        hint = QLabel("Drag pages to their new positions, then click Apply order.")
        hint.setStyleSheet("color: #5f6368;")
        layout.addWidget(hint)
        self.progress = QLabel("Loading previews…")
        self.progress.setStyleSheet("color: #5f6368;")
        layout.addWidget(self.progress)

        self.list = PageGridWidget(grid=True)
        layout.addWidget(self.list)

        controls = QHBoxLayout()
        earlier_button = QPushButton("Move Earlier")
        later_button = QPushButton("Move Later")
        self.grid_toggle = QCheckBox("Grid view")
        self.grid_toggle.setChecked(True)
        self.grid_toggle.toggled.connect(self._on_grid_toggled)
        earlier_button.clicked.connect(lambda: self._move_selected(-1))
        later_button.clicked.connect(lambda: self._move_selected(1))
        controls.addWidget(earlier_button)
        controls.addWidget(later_button)
        controls.addSpacing(12)
        controls.addWidget(self.grid_toggle)
        controls.addStretch(1)
        layout.addLayout(controls)

        if not _QT_PDF_AVAILABLE:
            self.progress.setText("Thumbnails unavailable; reordering needs QtPdf.")
        else:
            self._loader = PdfThumbnailLoader(path, self.list.thumbnail_box())
            self._loader.loaded.connect(self._on_loaded)
            self._loader.rendered.connect(self._on_rendered)
            self._loader.failed.connect(self._set_message)
            self._loader.start()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Apply order")
        layout.addWidget(buttons)

    @Slot(int)
    def _on_loaded(self, page_count: int) -> None:
        for index in range(page_count):
            item = QListWidgetItem(f"Page {index + 1}")
            item.setData(Qt.ItemDataRole.UserRole, index + 1)
            # Pin the row/cell size so hit-testing stays correct while icons stream in.
            item.setSizeHint(self.list.item_size())
            self.list.addItem(item)
        self._total = page_count
        self._rendered = 0
        self.progress.setText(f"Rendering previews… 0/{page_count}")

    @Slot(int, QImage)
    def _on_rendered(self, index: int, image: QImage) -> None:
        item = self.list.item(index)
        if item is not None:
            self.list.set_thumbnail(item, image)
        self._rendered += 1
        self.progress.setText(
            f"Rendering previews… {self._rendered}/{self._total}"
            if self._rendered < self._total
            else ""
        )

    @Slot(str)
    def _set_message(self, message: str) -> None:
        self.progress.setText(message)

    def _on_grid_toggled(self, checked: bool) -> None:
        self.list.set_grid(checked)

    def _move_selected(self, offset: int) -> None:
        self.list.move_row_to(self.list.currentRow(), self.list.currentRow() + offset)

    def order_spec(self) -> str:
        return ",".join(
            str(self.list.item(index).data(Qt.ItemDataRole.UserRole))
            for index in range(self.list.count())
        )

    def closeEvent(self, event) -> None:
        if self._loader is not None:
            self._loader.stop()
        super().closeEvent(event)
