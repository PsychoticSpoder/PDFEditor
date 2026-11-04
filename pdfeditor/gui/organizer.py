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

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QAction, QImage, QTransform
from PySide6.QtWidgets import (
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
    QRadioButton,
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
from .widgets import DropLineEdit, PageGridWidget

logger = logging.getLogger(__name__)


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


class ExportPagesDialog(QDialog):
    """Ask whether the exported pages go into one PDF or one PDF per page."""

    def __init__(self, scope_label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export pages")
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Export {scope_label} as:"))

        self.single_button = QRadioButton("One PDF containing every selected page")
        self.single_button.setChecked(True)
        self.per_page_button = QRadioButton("A separate PDF for each page")
        layout.addWidget(self.single_button)
        layout.addWidget(self.per_page_button)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Export")
        layout.addWidget(buttons)

    def single_pdf(self) -> bool:
        """True for one combined PDF, False for one file per page."""
        return self.single_button.isChecked()


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
        self._total = 0
        self._rendered = 0
        self._cleaned = False
        # Thumbnail cache: one identity per page appearance, so an edit only re-renders the
        # pages that actually changed (and a drag reorder re-renders nothing at all).
        self._keys: list[int] = []
        self._images: dict[int, QImage] = {}
        self._next_key = 0
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
        layout.addWidget(hint)

        self.grid = PageGridWidget(grid=True, multi_select=True)
        self.grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._show_context_menu)
        self.grid.itemSelectionChanged.connect(self._update_status)
        self.grid.rows_moved.connect(self.commit_order)
        layout.addWidget(self.grid, stretch=1)
        layout.addLayout(self._build_toolbar())

        self.status = QLabel("")
        self.status.setStyleSheet("color: #5f6368;")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        if path:
            self.load(path)
        else:
            self._update_status()

    # -- the document being edited ---------------------------------------------
    def _build_file_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.file_edit = DropLineEdit()
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
        try:
            document = PageOrganizerDocument(path)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Could not open %s in the organizer", path)
            self.status.setText(f"Error: {exc}")
            return False
        self.close_document()
        self.document = document
        self._cleaned = False
        if self.file_edit.text().strip() != str(path):
            self.file_edit.blockSignals(True)
            self.file_edit.setText(str(path))
            self.file_edit.blockSignals(False)
        self.reload()
        self.status.setText(f"Editing {Path(path).name}.")
        return True

    def close_document(self) -> None:
        """Stop the thumbnail thread and drop the temporary working copy."""
        if self._cleaned:
            return
        self._cleaned = True
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
            self._total = self._rendered = 0
            self.status.setText("Open a PDF to start editing.")
            return
        try:
            count = self.document.page_count()
        except Exception:  # noqa: BLE001 - the renderer reports the count as a fallback
            logger.exception("Could not count the pages of the working copy")
            count = 0
        self._images.clear()
        self._keys = [self._new_key() for _ in range(count)]
        self._redraw()

    def _new_key(self) -> int:
        """A fresh identity for a page's current appearance; also its cache key."""
        self._next_key += 1
        return self._next_key

    def _redraw(self) -> None:
        """Rebuild the grid for :attr:`_keys`, reusing every cached thumbnail.

        Only pages without a cached image are handed to the renderer, so rotating or
        deleting a single page no longer redraws the whole document.
        """
        self._stop_loader()
        self.grid.blockSignals(True)
        self.grid.clear()
        pending: list[int] = []
        for row, key in enumerate(self._keys):
            item = QListWidgetItem(f"Page {row + 1}")
            item.setData(Qt.ItemDataRole.UserRole, row + 1)
            item.setSizeHint(self.grid.item_size())
            self.grid.addItem(item)
            image = self._images.get(key)
            if image is None:
                pending.append(row)
            else:
                self.grid.set_thumbnail(item, image)
        self.grid.blockSignals(False)
        self._total = len(self._keys)
        self._rendered = self._total - len(pending)
        self._update_status()
        if pending:
            self._render_rows(pending)

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

    def _stop_loader(self) -> None:
        if self._loader is not None:
            self._loader.stop()
            self._loader = None

    @Slot(int, QImage)
    def _on_rendered(self, page_index: int, image: QImage) -> None:
        # The working copy always ends up in grid order, so a page index is also a row.
        if 0 <= page_index < len(self._keys):
            self._images[self._keys[page_index]] = image
            item = self.grid.item(page_index)
            if item is not None:
                self.grid.set_thumbnail(item, image)
        self._rendered += 1
        if self._rendered >= self._total:
            self._update_status()

    @Slot(str)
    def _set_message(self, message: str) -> None:
        self.status.setText(message)

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
        has_selection = bool(self.grid.selected_rows())
        loaded = self.document is not None
        for action in self._actions.values():
            needs_selection = bool(action.data())
            action.setEnabled(loaded and (has_selection or not needs_selection))
        if self.grid.count():
            self.status.setText(
                f"{self.grid.count()} page(s) · {len(self.grid.selected_rows())} selected"
            )

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

        Reordering only moves pages, so every cached thumbnail stays valid and nothing
        has to be rendered again.
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
        self._stop_loader()
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
        self._update_status()
        self.status.setText(f"Reordered to {spec}.")

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
        """Export the selection as one PDF, or as one PDF per page."""
        if self._require_document() is None:
            return
        spec = self.selected_spec() or None
        scope = f"page(s) {spec}" if spec else "every page"
        dialog = ExportPagesDialog(scope, self)
        if not dialog.exec():
            return
        if dialog.single_pdf():
            self.export_single(spec)
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
            lambda: written.extend(self.document.split(output_dir, spec, None)),
            lambda: f"Wrote {len(written)} page file(s) to {output_dir}.",
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
        self._stop_loader()
        document.revert()
        self.reload()
        self.status.setText("Reverted to the original document.")

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
        try:
            saved = document.save_as(output)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Could not save the organized PDF")
            self.status.setText(f"Error: {exc}")
            return None
        self.status.setText(f"Saved {saved}.")
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
        self.status.setText(describe() if callable(describe) else describe)

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
        self.status.setText(describe() if callable(describe) else describe)

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
