"""A single page-organizer view: every page as a thumbnail, plus the page operations.

This dialog is the one-stop place for page work: it shows all pages of a document as a
grid, supports Ctrl/Shift/Ctrl+A multi-selection, drag-to-reorder (a whole selection
moves as one block) and exposes rotate, delete, extract, insert, split, watermark and
metadata actions through both a toolbar and the right-click menu.

Every action is applied to a throwaway working copy held by
:class:`~pdfeditor.core.organizer.PageOrganizerDocument`, so the original file is never
touched, nothing is written until "Save As…" is used, and mistakes can be undone with
"Revert to Original".
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QImage, QTransform
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core import DEFAULT_WATERMARK_FONT_SIZE, DEFAULT_WATERMARK_OPACITY
from ..core.organizer import PageOrganizerDocument
from .preview import (
    PdfThumbnailLoader,
    _format_page_selection,
    _parse_spec_to_pages,
    qtpdf_available,
)
from .widgets import (
    ASPECT_ROLE,
    MAX_THUMB_SCALE,
    MIN_THUMB_SCALE,
    DropLineEdit,
    ExportModeSelector,
    PageGridWidget,
)

logger = logging.getLogger(__name__)

#: Documents with more pages than this render lazily (only the pages around the viewport).
_LAZY_THRESHOLD = 40

#: Writers whose thread has not stopped yet. Qt treats destroying a running QThread as fatal,
#: so a writer keeps itself alive here until its thread really finishes — what
#: :data:`pdfeditor.gui.preview._ACTIVE_LOADERS` already does for a render.
_ACTIVE_WRITERS: set[WorkingCopyWriter] = set()


class _WorkingCopyWriter(QObject):
    """Writes the document's pending edits; lives on a worker thread.

    The write is a whole-document pypdf pass — roughly a millisecond per page, and more for
    heavy pages — so it is far too slow for the GUI thread, where it used to hang the window
    for a moment after every reorder. ``previous`` is the render loader that was reading the
    working copy: it is waited for *here*, on this thread, because Windows cannot replace a
    file that another reader still has open.
    """

    written = Signal()
    failed = Signal(str)
    done = Signal()

    def __init__(
        self, document: PageOrganizerDocument, previous: PdfThumbnailLoader | None = None
    ) -> None:
        super().__init__()
        self._document = document
        self._previous = previous

    @Slot()
    def run(self) -> None:
        if self._previous is not None:
            self._previous.stop()
        try:
            # Reading the path is what writes the pending edits.
            logger.debug("Writing the pending page changes to %s", self._document.path)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Could not write the pending page changes")
            self.failed.emit(f"Could not write the page changes: {exc}")
        else:
            self.written.emit()
        self.done.emit()


class WorkingCopyWriter(QObject):
    """Owns the thread that writes a document's pending edits (see :meth:`run`)."""

    written = Signal()
    failed = Signal(str)

    def __init__(
        self, document: PageOrganizerDocument, previous: PdfThumbnailLoader | None = None
    ) -> None:
        super().__init__()
        self._thread = QThread()
        self._worker = _WorkingCopyWriter(document, previous)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.written.connect(self.written)
        self._worker.failed.connect(self.failed)
        self._worker.done.connect(self._thread.quit)
        self._thread.finished.connect(self._retire)
        _ACTIVE_WRITERS.add(self)

    def _retire(self) -> None:
        """Drop the keep-alive reference once the worker thread has really stopped."""
        _ACTIVE_WRITERS.discard(self)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Wait for the write to land (teardown, Revert and Save As need the file at rest)."""
        self._thread.quit()
        self._thread.wait()


class MetadataDialog(QDialog):
    """View and edit the PDF document metadata."""

    FIELDS = ("Title", "Author", "Subject", "Keywords", "Creator", "Producer")

    def __init__(self, metadata: dict[str, str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Document metadata")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.fields: dict[str, QLineEdit] = {}
        for name in self.FIELDS:
            edit = QLineEdit(metadata.get(name, ""))
            edit.setClearButtonEnabled(True)
            self.fields[name] = edit
            form.addRow(name, edit)
        layout.addLayout(form)

        hint = QLabel("Leave a field empty to clear that value.")
        hint.setStyleSheet("color: #5f6368;")
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Save metadata")
        layout.addWidget(buttons)

    def values(self) -> dict[str, str]:
        """The edited metadata, keyed as the core API expects (e.g. ``Title``)."""
        return {name: edit.text().strip() for name, edit in self.fields.items()}


class WatermarkDialog(QDialog):
    """Text, opacity, font size and scope for a watermark."""

    def __init__(self, selected_pages: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Apply watermark")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.text_edit = QLineEdit("CONFIDENTIAL")
        self.text_edit.setPlaceholderText("CONFIDENTIAL")
        self.opacity_spin = QDoubleSpinBox()
        self.opacity_spin.setRange(0.05, 1.0)
        self.opacity_spin.setSingleStep(0.05)
        self.opacity_spin.setValue(DEFAULT_WATERMARK_OPACITY)
        self.font_spin = QSpinBox()
        self.font_spin.setRange(8, 200)
        self.font_spin.setValue(DEFAULT_WATERMARK_FONT_SIZE)
        form.addRow("Text", self.text_edit)
        form.addRow("Opacity", self.opacity_spin)
        form.addRow("Font size", self.font_spin)
        layout.addLayout(form)

        self.selected_only = QCheckBox("Only the selected pages")
        self.selected_only.setChecked(selected_pages)
        self.selected_only.setEnabled(selected_pages)
        layout.addWidget(self.selected_only)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Apply watermark")
        layout.addWidget(buttons)

    def values(self) -> tuple[str, float, int]:
        """The watermark text, opacity and font size."""
        return (
            self.text_edit.text().strip(),
            self.opacity_spin.value(),
            self.font_spin.value(),
        )

    def only_selected(self) -> bool:
        """True when the watermark should be limited to the current selection."""
        return self.selected_only.isChecked() and self.selected_only.isEnabled()


def _documents_label(count: int, size: int) -> str:
    """Describe ``count`` exported files of ``size`` pages, e.g. ``"2 documents of 6 pages"``."""
    return f"{count} {'document' if count == 1 else 'documents'} of {size} pages"


def _never_widen(label: QLabel) -> None:
    """Let a word-wrapped label wrap freely instead of dictating the panel's width.

    ``QLabel`` breaks lines at spaces only, and it reports its widest unbreakable run as part of
    its minimum width. The reorder summary is one such run — ``"Reordered to 2,3,4,…,43,1."`` has
    no spaces in the page spec — so its minimum grew to the whole line (1440 px for 43 pages).
    That minimum propagated up through the layout: the panel, then the window, grew with it, and
    with it the page grid, until every page fitted on a single row of thumbnails.

    Ignoring the horizontal hint leaves the width to the layout. The height still follows the
    text because the height-for-width flag ``QLabel`` set is kept.
    """
    policy = label.sizePolicy()
    policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
    label.setSizePolicy(policy)


class ExportPagesDialog(QDialog):
    """Ask how the exported pages should be split up.

    ``page_count`` is how many pages the export covers; it is handed to the shared
    :class:`~pdfeditor.gui.widgets.ExportModeSelector`, which uses it to warn about a chunked
    split that leaves a shorter final document (see :meth:`uneven_split
    <pdfeditor.gui.widgets.ExportModeSelector.uneven_split>`).
    """

    def __init__(
        self, scope_label: str, page_count: int = 0, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export pages")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Export {scope_label} as:"))

        self.selector = ExportModeSelector(page_count)
        layout.addWidget(self.selector)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Export")
        layout.addWidget(buttons)

    def mode(self) -> str:
        """The chosen export: one combined PDF, one PDF per page, or N-page documents."""
        return self.selector.mode()

    def chunk_size(self) -> int:
        """How many pages go into each document in chunked mode."""
        return self.selector.chunk_size()

    def accept(self) -> None:
        """Close the dialog, asking first about a split that does not divide evenly."""
        if self._confirm_uneven_split():
            super().accept()

    def _confirm_uneven_split(self) -> bool:
        """Ask about a short final document; ``True`` means the export should go ahead.

        Ten pages as six-page documents gives 6 + 4, which is usually a slip but sometimes
        wanted, so it is warned about rather than blocked.
        """
        split = self.selector.uneven_split()
        if split is None:
            return True
        full, remainder = split
        chunk = self.selector.chunk_size()
        files = [_documents_label(full, chunk)] if full else []
        files.append(_documents_label(1, remainder))
        # An uneven split always covers several pages, so the plural reads correctly here.
        answer = QMessageBox.question(
            self,
            "Uneven split",
            f"{self.selector.page_count} pages do not divide evenly into documents of "
            f"{chunk} pages.\n\nYou will get {' and '.join(files)}.\n\nExport anyway?",
        )
        return answer == QMessageBox.StandardButton.Yes


class PageOrganizerWidget(QWidget):
    """Every page of one PDF as a selectable, draggable thumbnail grid.

    Selection follows the usual desktop conventions: click, Ctrl+click to toggle,
    Shift+click for a range, Ctrl+A for everything, and dragging a selection moves those
    pages together. It carries its own "PDF to edit" row, so it works both embedded (the
    Single PDF Editing panel) and inside :class:`PageOrganizerDialog`.
    ``confirm_destructive`` controls the delete confirmation popup (tests switch it off).
    """

    saved = Signal(str)

    #: Order of the right-click menu / "More actions" menu; an empty tuple is a divider.
    MENU_LAYOUT: tuple[tuple[str, ...], ...] = (
        ("select_all",),
        (),
        ("rotate_left", "rotate_right", "rotate_180"),
        (),
        ("delete", "export", "insert"),
        (),
        ("watermark", "metadata"),
        (),
        ("revert", "save_as"),
    )

    def __init__(self, path: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.document: PageOrganizerDocument | None = None
        self.confirm_destructive = True
        self._loader: PdfThumbnailLoader | None = None
        #: The write of the pending edits in flight (see :meth:`_start_writer`), if any.
        self._writer: WorkingCopyWriter | None = None
        self._cleaned = False
        # Thumbnail cache: one identity per page appearance, so an edit only re-renders the
        # pages that actually changed (and a drag reorder re-renders nothing at all).
        self._keys: list[int] = []
        self._images: dict[int, QImage] = {}
        #: Page shape per identity; kept even when a thumbnail's pixmap is dropped.
        self._aspects: dict[int, float] = {}
        self._next_key = 0
        # Lazy rendering: a big document only renders the pages around the viewport. The rows
        # with a render in flight are tracked so a scroll can tell what still needs asking for.
        self._lazy = False
        self._requested: set[int] = set()
        #: True while the zoom handle is held, so nothing re-renders underneath the drag.
        self._zoom_dragging = False
        #: An operation's own status message falls back to the page summary on its own.
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.setInterval(4000)
        self._status_timer.timeout.connect(self._update_status)
        # Thumbnail zoom: dragging the slider only previews the value (label only); the cells
        # are resized and the pages re-rendered once it settles, which keeps dragging smooth.
        #: The value the slider is asking for, and the value the grid actually shows.
        self._thumbnail_scale = 100
        self._applied_scale = 100
        self._zoom_timer = QTimer(self)
        self._zoom_timer.setSingleShot(True)
        self._zoom_timer.setInterval(120)
        self._zoom_timer.timeout.connect(self._commit_zoom)
        # Layout coalescing: when a streamed page changes its cell width (a landscape page in
        # a portrait document), one relayout is requested once the burst settles instead of
        # one per page — a per-page relayout is what made long documents crawl while loading.
        # Connected to the grid further below, once the grid exists.
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.setInterval(60)
        # Scroll/resize debounce: whichever pages have scrolled into view are rendered once the
        # view settles, rather than on every pixel of movement.
        self._view_timer = QTimer(self)
        self._view_timer.setSingleShot(True)
        self._view_timer.setInterval(120)
        self._view_timer.timeout.connect(self._render_needed)
        self._actions = self._build_actions()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._build_file_row())

        hint = QLabel(
            "Drag pages to rearrange them, then select pages (Ctrl/Shift/Ctrl+A) and "
            "right-click for page actions. Edits are temporary until you Save As…"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #5f6368;")
        _never_widen(hint)
        layout.addWidget(hint)

        self.grid = PageGridWidget(grid=True, multi_select=True)
        self.grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._show_context_menu)
        self.grid.itemSelectionChanged.connect(self._update_status)
        self.grid.rows_moved.connect(self.commit_order)
        self._layout_timer.timeout.connect(self.grid.relayout)
        self.grid.verticalScrollBar().valueChanged.connect(self._on_view_moved)
        self.grid.horizontalScrollBar().valueChanged.connect(self._on_view_moved)
        layout.addWidget(self.grid, stretch=1)
        layout.addLayout(self._build_toolbar())

        self.status = QLabel("")
        self.status.setStyleSheet("color: #5f6368;")
        self.status.setWordWrap(True)
        _never_widen(self.status)
        layout.addWidget(self.status)

        if path:
            self.load(path)
        else:
            self._update_status()

    # -- the document being edited ---------------------------------------------
    def _build_file_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        # Only a PDF may be dropped here: anything else is refused up front rather than handed
        # to the organizer (which would fail deep inside pypdf).
        self.file_edit = DropLineEdit(filter_text="PDF Files (*.pdf)")
        self.file_edit.setPlaceholderText("Drop or choose the PDF to edit")
        self.file_edit.textChanged.connect(self._on_file_text_changed)
        self.open_button = QPushButton("Open PDF")
        self.open_button.clicked.connect(self._choose_file)
        row.addWidget(QLabel("PDF to edit"))
        row.addWidget(self.file_edit, stretch=1)
        row.addWidget(self.open_button)
        return row

    def load(self, path: str) -> bool:
        """Open ``path`` as the document to edit; False (with a message) when it fails."""
        candidate = Path(path).expanduser()
        if not candidate.is_file() or candidate.suffix.lower() != ".pdf":
            logger.warning("Not a PDF file: %s", path)
            self.status.setText(f"Error: Not a PDF file: {candidate.name}")
            return False
        try:
            document = PageOrganizerDocument(path)
        except Exception as exc:  # noqa: BLE001
            # A file that cannot be read is a user-input problem, not a bug: one warning line,
            # with the traceback still available at debug level.
            logger.warning("Could not open %s in the organizer: %s", path, exc)
            logger.debug("Open failure detail", exc_info=True)
            self.status.setText(f"Error: {candidate.name} could not be read as a PDF.")
            return False
        self.close_document()
        self.document = document
        self._cleaned = False
        if self.file_edit.text().strip() != str(path):
            self.file_edit.blockSignals(True)
            self.file_edit.setText(str(path))
            self.file_edit.blockSignals(False)
        self.reload()
        self._flash_status(f"Editing {Path(path).name}.")
        return True

    def close_document(self) -> None:
        """Stop the worker threads and drop the temporary working copy."""
        if self._cleaned:
            return
        self._cleaned = True
        self._view_timer.stop()
        # A write still in flight must land before the file it is writing is deleted.
        self._stop_writer()
        self._stop_loader()
        if self.document is not None:
            self.document.close()
            self.document = None

    def closeEvent(self, event) -> None:
        """Release the working copy and the render thread when the widget goes away."""
        self.close_document()
        super().closeEvent(event)

    def _require_document(self) -> PageOrganizerDocument | None:
        """The document being edited, or None (with a nudge) when none is open."""
        if self.document is None:
            self.status.setText("Open a PDF first.")
        return self.document

    def _choose_file(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF Files (*.pdf)")
        if file_name:
            self.file_edit.setText(file_name)

    def _on_file_text_changed(self, text: str) -> None:
        """Open a newly nominated file (typed, pasted or dropped)."""
        nomination = Path(text.strip()).expanduser()
        if not text.strip() or not nomination.is_file():
            return
        if self.document is not None and nomination.resolve() == self.document.source:
            return
        self.load(str(nomination))

    # -- construction ----------------------------------------------------------
    def _build_actions(self) -> dict[str, QAction]:
        """Every action, shared by the toolbar buttons, menus and shortcuts."""
        actions: dict[str, QAction] = {}

        def add(
            key: str,
            text: str,
            handler,
            shortcut: str | None = None,
            needs_selection: bool = False,
            tip: str = "",
        ) -> None:
            action = QAction(text, self)
            action.setToolTip(tip or text)
            action.triggered.connect(handler)
            if shortcut:
                action.setShortcut(shortcut)
                self.addAction(action)
            action.setData(needs_selection)
            actions[key] = action

        add(
            "rotate_left",
            "Rotate Left",
            lambda: self.rotate_selected(-90),
            "Ctrl+L",
            tip="Rotate the selected pages (or every page) 90° counter-clockwise",
        )
        add(
            "rotate_right",
            "Rotate Right",
            lambda: self.rotate_selected(90),
            "Ctrl+R",
            tip="Rotate the selected pages (or every page) 90° clockwise",
        )
        add("rotate_180", "Rotate 180°", lambda: self.rotate_selected(180), "Ctrl+Shift+R")
        add(
            "delete",
            "Delete Pages",
            self.delete_selected,
            "Del",
            needs_selection=True,
            tip="Remove the selected pages from the document",
        )
        add(
            "export",
            "Export Pages…",
            self.export_pages,
            "Ctrl+E",
            tip="Save the selected pages (or every page) as one PDF or one PDF per page",
        )
        add("insert", "Insert Pages…", self.insert_pages, "Ctrl+I")
        add("watermark", "Watermark…", self.apply_watermark, "Ctrl+Shift+W")
        add("metadata", "Metadata…", self.edit_metadata, "Ctrl+M")
        add("select_all", "Select All Pages", self.select_all, "Ctrl+A")
        add("revert", "Revert to Original", self.revert)
        add("save_as", "Save As…", self.save_as, "Ctrl+S")
        return actions

    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        for key in ("rotate_left", "rotate_right", "delete", "export", "metadata"):
            row.addWidget(self._tool_button(key))
        self.more_button = QToolButton()
        self.more_button.setText("More actions")
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_button.setMenu(self.build_actions_menu())
        row.addWidget(self.more_button)
        row.addSpacing(12)
        row.addWidget(QLabel("Zoom"))
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(round(MIN_THUMB_SCALE * 100), round(MAX_THUMB_SCALE * 100))
        self.zoom_slider.setSingleStep(25)
        self.zoom_slider.setPageStep(25)
        self.zoom_slider.setFixedWidth(140)
        # Tracking off so dragging the handle does not fire a value change (and a re-render)
        # for every pixel moved; only the label follows the drag.
        self.zoom_slider.setTracking(False)
        self.zoom_slider.setToolTip(
            "Scale the page thumbnails up to read the contents of a page in detail"
        )
        self.zoom_label = QLabel("100%")
        # A fixed width keeps the text from reflowing the toolbar (and so the grid) mid-drag.
        self.zoom_label.setFixedWidth(52)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_slider.sliderPressed.connect(self._on_zoom_pressed)
        self.zoom_slider.sliderReleased.connect(self._on_zoom_released)
        self.zoom_slider.sliderMoved.connect(self._preview_zoom)
        self.zoom_slider.valueChanged.connect(self._schedule_zoom)
        row.addWidget(self.zoom_slider)
        row.addWidget(self.zoom_label)
        row.addStretch(1)
        self.save_button = QPushButton("Save As…")
        self.save_button.setToolTip("Write the edited document out to a new file")
        self.save_button.clicked.connect(self.save_as)
        row.addWidget(self.save_button)
        return row

    def _tool_button(self, key: str) -> QPushButton:
        action = self._actions[key]
        button = QPushButton(action.text().replace("…", ""))
        button.setToolTip(action.toolTip())
        button.clicked.connect(action.trigger)
        return button

    def build_actions_menu(self) -> QMenu:
        """A fresh menu holding every page action (context menu + "More actions")."""
        menu = QMenu(self)
        for group in self.MENU_LAYOUT:
            if not group:
                menu.addSeparator()
                continue
            for key in group:
                menu.addAction(self._actions[key])
        return menu

    # -- thumbnails ------------------------------------------------------------
    def reload(self) -> None:
        """Rebuild the grid from scratch (first load, and after Revert)."""
        self._stop_loader()
        if self.document is None:
            self.grid.clear()
            self._keys = []
            self._images.clear()
            self._aspects.clear()
            self._requested = set()
            self._lazy = False
            self.status.setText("Open a PDF to start editing.")
            return
        try:
            count = self.document.page_count()
        except Exception:  # noqa: BLE001 - the renderer reports the count as a fallback
            logger.exception("Could not count the pages of the working copy")
            count = 0
        self._images.clear()
        self._aspects.clear()
        self._keys = [self._new_key() for _ in range(count)]
        # A small document is rendered whole; a big one only where it is being looked at.
        self._lazy = count > _LAZY_THRESHOLD
        self._redraw()

    def _new_key(self) -> int:
        """A fresh identity for a page's current appearance; also its cache key."""
        self._next_key += 1
        return self._next_key

    def _redraw(self) -> None:
        """Rebuild the grid for :attr:`_keys`, reusing every cached thumbnail.

        Only pages that actually need a thumbnail are handed to the renderer, so rotating or
        deleting a single page no longer redraws the whole document — and a huge document only
        renders the part of it that is on screen.
        """
        self._stop_loader()
        self.grid.blockSignals(True)
        self.grid.clear()
        for row, key in enumerate(self._keys):
            item = QListWidgetItem(f"Page {row + 1}")
            item.setData(Qt.ItemDataRole.UserRole, row + 1)
            item.setSizeHint(self.grid.item_size())
            aspect = self._aspects.get(key)
            if aspect is not None and self.grid.is_grid():
                # A page whose pixmap was dropped still knows its shape, so its cell holds.
                item.setData(ASPECT_ROLE, aspect)
                item.setSizeHint(self.grid.cell_size_for_aspect(aspect))
            self.grid.addItem(item)
            image = self._images.get(key)
            if image is not None:
                self.grid.set_thumbnail(item, image)
        self.grid.blockSignals(False)
        self._update_status()
        self._render_needed()

    def _render_rows(self, rows: list[int]) -> None:
        """Ask the worker thread for the thumbnails of ``rows`` only."""
        if not qtpdf_available():
            self._set_message("Thumbnails unavailable (QtPdf missing); page actions still work.")
            return
        self._loader = PdfThumbnailLoader(
            str(self.document.path), self.grid.thumbnail_box(), pages=rows
        )
        self._loader.rendered.connect(self._on_rendered)
        self._loader.failed.connect(self._set_message)
        self._loader.start()
        self._requested = set(rows)

    def _stop_writer(self) -> None:
        """Wait for a background write to land.

        Only the paths that need the working copy at rest wait: teardown, Revert and Save As.
        Rendering never does — it lets the write finish on its own and renders afterwards,
        which is what keeps the window responsive while a long document is rewritten.
        """
        writer, self._writer = self._writer, None
        if writer is not None:
            writer.stop()

    def _start_writer(self, previous: PdfThumbnailLoader | None = None) -> bool:
        """Write the pending page changes on a worker thread; True when one was started.

        Rendering reads the working copy from disk, so the edits have to be written first —
        and for a long document that rewrite is far too slow for the GUI thread. Handing it to
        a worker keeps the window live: the render that wanted the file waits for
        :meth:`_on_written` instead of the window freezing until the write is done.

        ``previous`` is the render loader that was reading the file before this call: the
        writer waits for it, so the working copy is never replaced under an open reader.
        """
        if self._writer is not None or self.document is None:
            return False
        if not self.document.has_pending_edits:
            return False
        writer = WorkingCopyWriter(self.document, previous)
        writer.written.connect(self._on_written)
        writer.failed.connect(self._on_write_failed)
        self._writer = writer
        writer.start()
        return True

    @Slot()
    def _on_written(self) -> None:
        """The working copy is up to date again: render whatever the view still needs."""
        self._writer = None
        self._render_needed()

    @Slot(str)
    def _on_write_failed(self, message: str) -> None:
        """The write failed: say so, and let the next render or edit try again.

        The writer is dropped either way — it is the "a write is in flight" marker the render
        path checks, so keeping it would stop every later render from ever starting.
        """
        self._writer = None
        self._set_message(message)

    def _stop_loader(self, wait: bool = True) -> PdfThumbnailLoader | None:
        """Stop the current render, returning the loader that was stopped.

        ``wait=False`` asks the worker to stop without blocking the GUI thread — what a zoom
        change and a drag reorder want, since waiting here froze the window until the
        in-flight page finished. Teardown still waits (``wait=True``), because the temporary
        working copy cannot be deleted while the worker still has the file open on Windows;
        a background write waits for the returned loader on its own thread
        (:class:`_WorkingCopyWriter`), for the same reason.
        """
        loader, self._loader = self._loader, None
        self._requested = set()
        if loader is None:
            return None
        # Disconnect first: a render already in flight must not touch our state after we have
        # moved on (its images belong to the old resolution and would overwrite the new ones).
        for signal in (loader.rendered, loader.failed):
            try:
                signal.disconnect()
            except (RuntimeError, TypeError):
                pass
        if wait:
            loader.stop()
        else:
            loader.cancel()
        return loader

    # -- thumbnail zoom --------------------------------------------------------
    def set_thumbnail_scale(self, percent: int) -> None:
        """Zoom the page thumbnails to ``percent`` (100 = the default size).

        The grid's cells resize and every page is rendered again at the new resolution, so
        zooming in reveals more page detail instead of a blurry enlargement of a small
        thumbnail. The slider calls this once its value has settled; it is also safe to call
        directly (the slider and its label are kept in sync).
        """
        percent = min(max(percent, self.zoom_slider.minimum()), self.zoom_slider.maximum())
        self._zoom_timer.stop()
        # Keep the slider and its label in step even for a programmatic call.
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(percent)
        self.zoom_slider.blockSignals(False)
        self.zoom_label.setText(f"{percent}%")
        self._thumbnail_scale = percent
        if percent == self._applied_scale:
            # Compare against what the grid actually shows, not the requested value: a click
            # on the bar sets the request on press, so testing the request here would skip the
            # commit entirely and the zoom would never be applied.
            return
        self._commit_zoom()

    @Slot()
    def _on_zoom_pressed(self) -> None:
        """The handle was grabbed: show the value live but do no work until it is let go.

        The timer is deliberately left running: a click on the bar sets the value before this
        signal, so the queued commit must survive the press and be deferred (not cancelled)
        until the button comes up.
        """
        self._zoom_dragging = True

    @Slot()
    def _on_zoom_released(self) -> None:
        """The handle was released: only now is the grid resized and re-rendered.

        Reads :meth:`sliderPosition` because Qt emits ``sliderReleased`` *before* it applies
        the new value, so ``value()`` still holds the old one at this point.
        """
        self._zoom_dragging = False
        self._zoom_timer.stop()
        self.set_thumbnail_scale(self.zoom_slider.sliderPosition())

    @Slot(int)
    def _preview_zoom(self, percent: int) -> None:
        """Reflect the handle while it is dragged — label only, so dragging stays smooth."""
        self.zoom_label.setText(f"{percent}%")

    @Slot(int)
    def _schedule_zoom(self, percent: int) -> None:
        """Queue a zoom change made without dragging (keyboard, or a click on the bar).

        Nothing is queued while the handle is held: the re-render happens once on release,
        so dragging the slider never competes with pages re-rendering underneath it.
        """
        self.zoom_label.setText(f"{percent}%")
        if self._zoom_dragging or percent == self._thumbnail_scale:
            return
        self._thumbnail_scale = percent
        self._zoom_timer.start()

    def _zoom_held(self) -> bool:
        """True while the user is still using the zoom slider.

        A press that lands on the *bar* rather than exactly on the handle changes the value
        straight away and emits no ``sliderPressed``/``sliderReleased`` (and leaves
        ``isSliderDown()`` False), so the mouse button state is the only reliable sign that a
        drag is in progress. Without this the queued re-render fired ~120 ms into the drag —
        which is why zooming only lagged *sometimes*, depending on where the press landed.
        """
        return (
            self._zoom_dragging
            or self.zoom_slider.isSliderDown()
            or bool(QApplication.mouseButtons() & Qt.MouseButton.LeftButton)
        )

    def _commit_zoom(self) -> None:
        """Resize the grid's cells to the chosen zoom and re-render at the new resolution.

        While the slider is still being dragged the commit is postponed (by restarting the
        timer), so no page is ever re-rendered underneath an in-progress drag.
        """
        if self._zoom_held():
            self._zoom_timer.start()
            return
        self.grid.set_thumbnail_scale(self._thumbnail_scale / 100.0)
        self._applied_scale = self._thumbnail_scale
        self._rerender_thumbnails()

    def _rerender_thumbnails(self) -> None:
        """Render every page again at the current zoom, reusing the existing grid rows.

        Unlike :meth:`_redraw` this does not rebuild the items — a zoom never changes the page
        order or count — so committing a new zoom stays cheap and snappy.
        """
        if self.document is None:
            return
        # Non-blocking: the old worker stops on its own while the new render starts, so the
        # zoom slider stays responsive even when a page render is in flight.
        self._stop_loader(wait=False)
        self._images.clear()
        # The page shapes do not change with the zoom, so the cells keep their sizes and only
        # the images are re-rendered (in lazy mode, just the ones on screen).
        self._render_needed()

    @Slot(int, QImage)
    def _on_rendered(self, page_index: int, image: QImage) -> None:
        # The working copy always ends up in grid order, so a page index is also a row.
        if 0 <= page_index < len(self._keys):
            key = self._keys[page_index]
            self._images[key] = image
            self._aspects[key] = image.width() / image.height()
            item = self.grid.item(page_index)
            if item is not None:
                hint_before = item.sizeHint()
                self.grid.set_thumbnail(item, image)
                if item.sizeHint() != hint_before:
                    # A wider cell shifts every following cell, so ask for one coalesced
                    # relayout rather than letting each streamed page invalidate the layout.
                    self._layout_timer.start()
        self._requested.discard(page_index)
        if not self._requested:
            # A finished render settles the layout straight away: while images stream in, Qt
            # defers its own relayout, so without this the last page could be drawn at a stale
            # offset until the next idle moment (the "overlapping pages" at high zoom).
            # The status summary is deliberately left alone here: refreshing it would wipe an
            # operation's message as soon as a (possibly scroll-triggered) render finished.
            self._layout_timer.stop()
            self.grid.relayout()

    @Slot(str)
    def _set_message(self, message: str) -> None:
        self.status.setText(message)

    # -- lazy rendering --------------------------------------------------------
    @Slot()
    def _on_view_moved(self) -> None:
        """The viewport scrolled; pick up whichever pages are on screen once it settles."""
        self._view_timer.start()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._view_timer.start()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._view_timer.start()

    def _visible_rows(self) -> list[int] | None:
        """The rows around the viewport, or None while the view cannot be measured yet.

        Uses each item's real rect rather than :meth:`QListView.indexAt`, which is unreliable
        straight after the grid is rebuilt (before the view has laid out) and used to report
        the last page as "visible" — rendering the whole document at once.
        """
        count = self.grid.count()
        if count == 0:
            return []
        viewport = self.grid.viewport().rect()
        if viewport.width() <= 1 or viewport.height() <= 1:
            return None
        # Look at a band half a viewport tall above and below, so short scrolls stay instant
        # without keeping far more pages in memory than can be seen at once.
        pad = max(viewport.height() // 2, 1)
        band = viewport.adjusted(0, -pad, 0, pad)
        rows: list[int] = []
        for row in range(count):
            rect = self.grid.visualItemRect(self.grid.item(row))
            if rect.isValid() and not rect.isEmpty() and rect.intersects(band):
                rows.append(row)
        return rows or None

    def _want_rows(self) -> list[int] | None:
        """The rows that should have a thumbnail now (None while the view is unmeasured)."""
        if not self._lazy:
            return list(range(len(self._keys)))
        return self._visible_rows()

    def _render_needed(self) -> None:
        """Render whatever the view needs now, and drop what has scrolled far away.

        A small document is rendered whole (what the common case and the tests expect); a big
        one renders only the pages around the viewport, so opening or zooming a 500-page
        document no longer renders every page at once.
        """
        if self.document is None:
            return
        want = self._want_rows()
        if want is None:
            # The grid has not been laid out yet; try again once it has a size.
            if self.isVisible():
                self._view_timer.start()
            return
        if self._lazy:
            self._evict_far_rows(set(want))
        missing = [row for row in want if self._keys[row] not in self._images]
        if self._loader is not None and set(missing) <= self._requested:
            return  # the render in flight already covers everything the view needs
        if self._writer is not None:
            return  # a write in flight asks for the render again once it lands
        previous = self._stop_loader(wait=False)
        if not missing:
            return
        # Rendering reads the working copy from disk, so any pending edit has to be written
        # first — on a worker thread, which is why this defers the render instead of blocking.
        if not self._start_writer(previous):
            self._render_rows(missing)

    def _evict_far_rows(self, keep: set[int]) -> None:
        """Drop the thumbnails of pages well outside the viewport, to bound memory."""
        for row, key in enumerate(self._keys):
            if row in keep:
                continue
            item = self.grid.item(row)
            # Keyed off the item, not the cache: a zoom empties the cache but the items keep
            # their old pixmaps until they are cleared here.
            if item is None or not self.grid.has_thumbnail(item):
                continue
            self._images.pop(key, None)
            self.grid.clear_thumbnail(item)

    # -- selection -------------------------------------------------------------
    def selected_spec(self) -> str:
        """The current selection as a 1-based spec such as ``"1,3,5-7"``."""
        return _format_page_selection([row + 1 for row in self.grid.selected_rows()])

    def select_all(self) -> None:
        """Select every page (what Ctrl+A and the context menu call)."""
        self.grid.selectAll()
        self._update_status()

    def _require_selection(self) -> str | None:
        spec = self.selected_spec()
        if not spec:
            self.status.setText("Select one or more pages first.")
            return None
        return spec

    def _update_status(self) -> None:
        """Show the page summary and refresh every action's enabled state."""
        self._status_timer.stop()
        has_selection = bool(self.grid.selected_rows())
        loaded = self.document is not None
        for action in self._actions.values():
            needs_selection = bool(action.data())
            action.setEnabled(loaded and (has_selection or not needs_selection))
        if self.grid.count():
            self.status.setText(
                f"{self.grid.count()} page(s) · {len(self.grid.selected_rows())} selected"
            )

    def _flash_status(self, message: str) -> None:
        """Show what an operation did, then fall back to the page summary on its own.

        The summary used to come back only when the next render finished, which with lazy
        rendering can be a scroll away — so an operation's message (e.g. "Reordered to
        2,1,3,4.") looked stuck. A timer restores the summary regardless of rendering.
        """
        self.status.setText(message)
        self._status_timer.start()

    @Slot()
    def _show_context_menu(self, pos) -> None:
        # Right-clicking outside the selection acts on the page under the pointer.
        item = self.grid.itemAt(pos)
        if item is not None and not item.isSelected():
            self.grid.clearSelection()
            item.setSelected(True)
            self.grid.setCurrentItem(item)
        self._update_status()
        self.build_actions_menu().exec(self.grid.viewport().mapToGlobal(pos))

    # -- page operations -------------------------------------------------------
    def rotate_selected(self, angle: int) -> None:
        """Rotate the selection; the previews turn instantly, without being re-rendered."""
        pages = self.selected_spec() or None
        transform = QTransform().rotate(angle)
        keys = list(self._keys)
        for row in self._rows_for(pages):
            cached = self._images.pop(keys[row], None)
            keys[row] = self._new_key()
            if cached is not None:
                # A rotated page is just the old preview turned, so no render is needed.
                self._images[keys[row]] = cached.transformed(transform)
        target = f"page(s) {pages}" if pages else "every page"
        self._mutate(
            lambda doc: doc.rotate(pages, angle),
            f"Rotated {target} by {angle}°.",
            keys=keys,
        )

    def _keys_with_fresh(self, rows: list[int]) -> tuple[list[int], list[int]]:
        """New identities for the pages at ``rows``, plus their old thumbnails to drop."""
        keys = list(self._keys)
        stale: list[int] = []
        for row in rows:
            if 0 <= row < len(keys):
                stale.append(keys[row])
                keys[row] = self._new_key()
        return keys, stale

    def _rows_for(self, pages: str | None) -> list[int]:
        """The grid rows covered by a 1-based page spec (every row when ``None``)."""
        if pages is None:
            return list(range(len(self._keys)))
        return [
            number - 1
            for number in sorted(_parse_spec_to_pages(pages))
            if 1 <= number <= len(self._keys)
        ]

    def _all_pages_spec(self) -> str:
        """``"1-4"`` style spec covering every page currently in the grid."""
        return _format_page_selection(list(range(1, self.grid.count() + 1)))

    @Slot()
    def commit_order(self) -> None:
        """Persist the grid's order to the working copy (called when a drag ends).

        Reordering only moves pages, so every cached thumbnail stays valid and nothing has to
        be rendered again. The render in flight is stopped without waiting for it — the same
        non-blocking stop the zoom uses: its images describe the order from before the drag,
        so they have to be dropped, but waiting for the page it is drawing is what froze the
        window on a slow document. The new order is then written in the background.
        """
        order = [
            int(self.grid.item(row).data(Qt.ItemDataRole.UserRole))
            for row in range(self.grid.count())
        ]
        if not order or order == list(range(1, len(order) + 1)):
            return
        spec = ",".join(str(page) for page in order)
        document = self._require_document()
        if document is None:
            return
        previous = self._stop_loader(wait=False)
        try:
            document.reorder(spec)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Could not reorder the working copy")
            self.status.setText(f"Error: {exc}")
            self.reload()
            return
        self._keys = [self._keys[index - 1] for index in order]
        for row in range(self.grid.count()):
            item = self.grid.item(row)
            item.setText(f"Page {row + 1}")
            item.setData(Qt.ItemDataRole.UserRole, row + 1)
        # Write it now rather than when the next render asks for the file, so a scroll, a zoom
        # or the next drag finds the working copy up to date instead of paying the rewrite.
        self._start_writer(previous)
        self._update_status()
        self._flash_status(f"Reordered to {spec}.")

    def delete_selected(self) -> None:
        """Delete the selected pages from the working copy."""
        spec = self._require_selection()
        if spec is None:
            return
        if self.confirm_destructive:
            answer = QMessageBox.question(
                self, "Delete pages", f"Delete page(s) {spec} from the working copy?"
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        doomed = self._rows_for(spec)
        self._mutate(
            lambda doc: doc.delete(spec),
            f"Deleted page(s) {spec}.",
            keys=[key for row, key in enumerate(self._keys) if row not in set(doomed)],
            stale=[self._keys[row] for row in doomed],
        )

    def export_pages(self) -> None:
        """Export the selection as one PDF, one PDF per page, or in fixed-size documents."""
        if self._require_document() is None:
            return
        spec = self.selected_spec() or None
        scope = f"page(s) {spec}" if spec else "every page"
        dialog = ExportPagesDialog(scope, len(self._rows_for(spec)), self)
        if not dialog.exec():
            return
        mode = dialog.mode()
        if mode == ExportModeSelector.SINGLE:
            self.export_single(spec)
        elif mode == ExportModeSelector.CHUNKED:
            self.export_chunked(spec, dialog.chunk_size())
        else:
            self.export_per_page(spec)

    def export_single(self, spec: str | None = None) -> None:
        """Write the given pages (or every page) into one PDF."""
        document = self._require_document()
        if document is None:
            return
        pages = spec or self._all_pages_spec()
        output, _ = QFileDialog.getSaveFileName(
            self,
            "Export pages to one PDF",
            document.suggest_output_name("_selected"),
            "PDF Files (*.pdf)",
        )
        if not output:
            return
        self._export(
            lambda: self.document.extract(pages, output),
            f"Exported page(s) {pages} to {output}.",
        )

    def export_per_page(self, spec: str | None = None) -> None:
        """Write one PDF per page into a folder."""
        output_dir = QFileDialog.getExistingDirectory(self, "Choose a folder for the page PDFs")
        if not output_dir:
            return
        written: list[Path] = []
        self._export(
            lambda: written.extend(self.document.split(output_dir, spec, None, unique_names=True)),
            lambda: f"Wrote {len(written)} page file(s) to {output_dir}.",
        )

    def export_chunked(self, spec: str | None = None, pages_per_file: int = 2) -> None:
        """Write the pages into a folder as documents of ``pages_per_file`` pages each."""
        output_dir = QFileDialog.getExistingDirectory(self, "Choose a folder for the split PDFs")
        if not output_dir:
            return
        written: list[Path] = []
        self._export(
            lambda: written.extend(
                self.document.split(output_dir, spec, None, pages_per_file, unique_names=True)
            ),
            lambda: (
                f"Wrote {len(written)} file(s) of up to {pages_per_file} page(s) to {output_dir}."
            ),
        )

    def insert_pages(self) -> None:
        """Insert the pages of another PDF before the selected page."""
        document = self._require_document()
        if document is None:
            return
        source, _ = QFileDialog.getOpenFileName(
            self, "Choose the PDF to insert", "", "PDF Files (*.pdf)"
        )
        if not source:
            return
        rows = self.grid.selected_rows()
        default_position = max(rows) + 2 if rows else 1
        count = document.page_count()
        position, accepted = QInputDialog.getInt(
            self,
            "Insert position",
            f"Insert before page (1-{count + 1}):",
            default_position,
            1,
            count + 1,
        )
        if not accepted:
            return
        before = len(self._keys)
        split_at = min(max(position - 1, 0), before)

        def new_keys() -> list[int]:
            """Only the inserted pages are new; the rest keep their thumbnails."""
            inserted = max(document.page_count() - before, 0)
            fresh = [self._new_key() for _ in range(inserted)]
            return self._keys[:split_at] + fresh + self._keys[split_at:]

        self._mutate(
            lambda doc: doc.insert(source, position),
            f"Inserted {Path(source).name} before page {position}.",
            keys=new_keys,
        )

    def apply_watermark(self) -> None:
        """Add a text watermark to the selection or to the whole document."""
        spec = self.selected_spec() or None
        dialog = WatermarkDialog(selected_pages=bool(spec), parent=self)
        if not dialog.exec():
            return
        text, opacity, font_size = dialog.values()
        pages = spec if dialog.only_selected() else None
        keys, stale = self._keys_with_fresh(self._rows_for(pages))
        target = f"page(s) {pages}" if pages else "every page"
        self._mutate(
            lambda doc: doc.watermark(pages, text, opacity, font_size),
            f"Watermarked {target} with '{text}'.",
            keys=keys,
            stale=stale,
        )

    def edit_metadata(self) -> None:
        """View and edit the document metadata."""
        document = self._require_document()
        if document is None:
            return
        dialog = MetadataDialog(document.get_metadata(), self)
        if not dialog.exec():
            return
        values = dialog.values()
        # Metadata does not change how any page looks, so keep every thumbnail.
        self._mutate(
            lambda doc: doc.set_metadata(values),
            "Metadata updated.",
            keys=list(self._keys),
        )

    def revert(self) -> None:
        """Throw away every edit and return to the original document."""
        document = self._require_document()
        if document is None:
            return
        # A write still running would put the edits back over the reverted file.
        self._stop_writer()
        self._stop_loader()
        document.revert()
        self.reload()
        self._flash_status("Reverted to the original document.")

    def save_as(self) -> str | None:
        """Write the working copy out to a file the user picks."""
        document = self._require_document()
        if document is None:
            return None
        output, _ = QFileDialog.getSaveFileName(
            self,
            "Save organized PDF",
            document.suggest_output_name(),
            "PDF Files (*.pdf)",
        )
        if not output:
            return None
        # Save As copies the working copy, so no background write may be touching it.
        self._stop_writer()
        try:
            saved = document.save_as(output)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Could not save the organized PDF")
            self.status.setText(f"Error: {exc}")
            return None
        self._flash_status(f"Saved {saved}.")
        self.saved.emit(str(saved))
        return str(saved)

    def saved_path(self) -> str | None:
        """The file written by the last successful Save As, if any."""
        if self.document is None:
            return None
        path = self.document.saved_path
        return str(path) if path else None

    # -- helpers ---------------------------------------------------------------
    def _mutate(self, operation, describe, keys=None, stale=()) -> None:
        """Apply a change to the working copy, then redraw only what changed.

        ``keys`` may be the new list of page identities (one per row), or a callable
        returning it when the effect on the page list is only known afterwards (Insert).
        ``stale`` are the identities whose thumbnails must be dropped, i.e. the pages
        whose appearance changed. Both default to a full rebuild.
        """
        document = self._require_document()
        if document is None:
            return
        self._stop_loader()
        try:
            operation(document)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Page organizer action failed")
            self.status.setText(f"Error: {exc}")
            return
        for key in stale:
            self._images.pop(key, None)
        if keys is None:
            self.reload()
        else:
            self._keys = list(keys() if callable(keys) else keys)
            self._redraw()
        self._flash_status(describe() if callable(describe) else describe)

    def _export(self, operation, describe) -> None:
        """Run an action that writes files outside the working copy; no grid reload."""
        if self._require_document() is None:
            return
        try:
            operation()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Page organizer export failed")
            self.status.setText(f"Error: {exc}")
            return
        self._flash_status(describe() if callable(describe) else describe)

    # -- lifecycle (see :meth:`close_document`) ---------------------------------


class PageOrganizerDialog(QDialog):
    """The organizer in its own window; wraps the same widget the panel embeds."""

    def __init__(self, path: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Organize pages - {Path(path).name}" if path else "Organize pages")
        self.setMinimumSize(900, 640)

        layout = QVBoxLayout(self)
        self.organizer = PageOrganizerWidget(path, self)
        layout.addWidget(self.organizer)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Release the temporary working copy when the window goes away.
        self.finished.connect(lambda _result: self.organizer.close_document())

    def saved_path(self) -> str | None:
        """The file written by the last successful Save As, if any."""
        return self.organizer.saved_path()
