"""Main application window for the PySide6 GUI."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QSettings, Qt, QThread, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from ..core import (
    DEFAULT_EXCEL_PAPER_SIZE,
    DEFAULT_EXCEL_PATTERN,
    DEFAULT_GLOB_PATTERN,
    DEFAULT_WATERMARK_FONT_SIZE,
    DEFAULT_WATERMARK_OPACITY,
    DEFAULT_WORD_PATTERN,
    DEFAULT_WORKERS,
    EXCEL_PAPER_SIZES,
    MAX_WORKERS,
    MIN_WORKERS,
    OUTPUT_DIR_ACTIONS,
    _unique_path,
    batch_excel_to_pdf,
    batch_pdf_to_text,
    batch_word_to_pdf,
    combine_pdfs,
    excel_to_pdf,
    images_to_pdf,
    pdf_to_text,
    print_pdf,
    word_to_pdf,
)
from .organizer import PageOrganizerWidget
from .theme import apply_theme
from .widgets import (
    ConversionPanel,
    ExportModeSelector,
    OrderedFileList,
    _directory_row,
    _existing_dir_from_input,
    _existing_file_from_input,
    _link_default_output,
    _output_dir_from_input,
    _output_file_from_input,
    _row_widget,
    _save_row,
)
from .workers import BatchWorker, SingleOpWorker

logger = logging.getLogger(__name__)

#: The "Single PDF Editing" panel (the page organizer) is first in the sidebar.
_SINGLE_PDF_ROW = 0

#: Minimum height of the Images→PDF list, so a conversion pane's list is never cramped.
_MIN_CONVERT_LIST_HEIGHT = 280


def _scrollable(widget: QWidget) -> QScrollArea:
    """Wrap a tab's content so tall panels stay scrollable."""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(widget)
    return area


def _image_split_targets(inputs: list[Path], output_dir: Path) -> list[tuple[Path, Path]]:
    """Where each image goes when every image becomes its own PDF.

    ``photo.png`` next to ``photo.jpg`` would both become ``photo.pdf``, so a clash is reported
    rather than letting one silently overwrite the other.
    """
    pairs = [(source, output_dir / f"{source.stem}.pdf") for source in inputs]
    names = [target.name for _source, target in pairs]
    clashes = sorted({name for name in names if names.count(name) > 1})
    if clashes:
        raise ValueError(f"Two images would become the same file: {', '.join(clashes)}")
    return pairs


def _print_in_order(paths: list[Path]) -> None:
    """Send each PDF to the printer, in the order the user arranged them."""
    for path in paths:
        print_pdf(path)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PDF Editor Tool")
        self.resize(960, 680)
        self._settings = QSettings("pdfeditor", "pdfeditor")
        saved_geometry = self._settings.value("window_geometry")
        if saved_geometry is not None:
            self.restoreGeometry(saved_geometry)

        central = QWidget()
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(14, 14, 14, 14)
        root_layout.setSpacing(12)

        header = QLabel("Edit, split, combine, convert, print, and batch process PDFs")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setStyleSheet("font-size: 16px; font-weight: 600;")
        root_layout.addWidget(header)

        hint = QLabel(
            "Tip: open a PDF in the Single PDF Editing panel, or drag files/folders into "
            "any field."
        )
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: #666666;")
        root_layout.addWidget(hint)

        # Dropping a PDF onto the window opens it in the Single PDF Editing panel, which owns
        # the only file picker now — there is no separate "current document" bar.
        self.setAcceptDrops(True)

        # Left action sidebar + a stack of operation panels (document-centric IA).
        self.panel_list = QListWidget()
        self.panel_list.setFixedWidth(170)
        self.panel_stack = QStackedWidget()

        # The organizer fills its page and scrolls internally, so it is not wrapped.
        panels: list[tuple[str, QWidget, bool]] = [
            ("Single PDF Editing", self._build_single_pdf_panel(), False),
            ("Bulk PDF Editing", self._build_batch_tab(), True),
            ("Merge", self._build_combine_tab(), True),
            ("Convert", self._build_convert_tab(), True),
            ("Print", self._build_print_tab(), True),
        ]

        for label, panel, scrollable in panels:
            self.panel_list.addItem(label)
            self.panel_stack.addWidget(_scrollable(panel) if scrollable else panel)

        self.panel_list.currentRowChanged.connect(self.panel_stack.setCurrentIndex)
        self.panel_list.setCurrentRow(_SINGLE_PDF_ROW)

        panels_row = QHBoxLayout()
        panels_row.addWidget(self.panel_list)
        panels_row.addWidget(self.panel_stack, stretch=1)
        root_layout.addLayout(panels_row, stretch=1)

        self.setCentralWidget(central)

        # Unified status bar: shared progress + a Cancel control for long operations.
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(220)
        self.progress.setVisible(False)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self._request_cancel)
        self.status.addPermanentWidget(self.progress)
        self.status.addPermanentWidget(self.cancel_button)
        self.open_folder_button = QPushButton("Open folder")
        self.open_folder_button.clicked.connect(self._open_current_folder)
        self.status.addPermanentWidget(self.open_folder_button)
        self.status.showMessage("Ready")

        # Menu bar with mnemonics and common shortcuts.
        file_menu = self.menuBar().addMenu("&File")
        quit_action = file_menu.addAction("&Quit")
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        view_menu = self.menuBar().addMenu("&View")
        self.fullscreen_action = view_menu.addAction("&Full Screen")
        self.fullscreen_action.setCheckable(True)
        self.fullscreen_action.setShortcut("F11")
        self.fullscreen_action.toggled.connect(self._toggle_fullscreen)
        help_menu = self.menuBar().addMenu("&Help")
        about_action = help_menu.addAction("&About")
        about_action.triggered.connect(self._show_about)

        self.batch_thread: QThread | None = None
        self.batch_worker: BatchWorker | None = None
        self._single_thread: QThread | None = None
        self._single_worker: SingleOpWorker | None = None

    def _set_busy(self, busy: bool) -> None:
        """Toggle interaction and the progress/cancel affordances while work runs."""
        self.panel_list.setEnabled(not busy)
        self.panel_stack.setEnabled(not busy)
        self.progress.setVisible(busy)
        self.cancel_button.setVisible(busy)
        if busy:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)

    def _request_cancel(self) -> None:
        """Ask the running batch to stop (honoured between files)."""
        if self.batch_worker is not None:
            self.batch_worker.request_cancel()
            self.status.showMessage("Cancelling…")

    def closeEvent(self, event) -> None:
        self._settings.setValue("window_geometry", self.saveGeometry())
        # Stop the organizer's render thread and drop its temporary working copy.
        self.single_pdf_panel.close_document()
        super().closeEvent(event)

    def open_document(self, path: str) -> None:
        """Show the Single PDF Editing panel and open ``path`` in it.

        Used when a PDF is dropped onto the window; the panel's own "PDF to edit" row is the
        file picker.
        """
        self.panel_list.setCurrentRow(_SINGLE_PDF_ROW)
        self.single_pdf_panel.load(path)

    def _on_organized(self, path: str) -> None:
        """Report an organizer Save As in the shared status bar."""
        self.status.showMessage(f"Saved organized PDF: {path}")

    @staticmethod
    def _dropped_pdf(event) -> Path | None:
        """The first dropped local file that is a PDF, or ``None`` when there is none.

        Drag-and-drop only ever opens one PDF: a spreadsheet or a folder dropped on the window
        used to be handed to the organizer, which then failed deep inside pypdf.
        """
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_file() and path.suffix.lower() == ".pdf":
                return path
        return None

    def dragEnterEvent(self, event) -> None:
        """Accept a dropped PDF, and refuse anything else without a doomed open attempt."""
        if self._dropped_pdf(event) is None:
            if event.mimeData().hasUrls():
                # Said once per drag (drag-move events are left alone) rather than silently.
                self.status.showMessage("Only PDF files can be dropped here to open them.", 8000)
            event.ignore()
            return
        event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        dropped = self._dropped_pdf(event)
        if dropped is None:
            event.ignore()
            return
        self.open_document(str(dropped))
        event.acceptProposedAction()

    def _open_current_folder(self) -> None:
        """Open the folder of the PDF the Single PDF Editing panel has loaded."""
        text = self.single_pdf_panel.file_edit.text().strip()
        if not text:
            return
        folder = Path(text).expanduser().parent
        if folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _show_about(self) -> None:
        QMessageBox.information(
            self,
            "About PDF Editor Tool",
            "A small toolkit to edit, split, combine, convert and batch-process PDFs.",
        )

    def _toggle_fullscreen(self, enabled: bool) -> None:
        """Switch between true full-screen (F11) and a maximized window."""
        if enabled:
            self.showFullScreen()
        else:
            self.showMaximized()

    def _build_single_pdf_panel(self) -> QWidget:
        """The single panel for editing one PDF: the embedded page organizer.

        Reorder, rotate, delete, export, insert, watermark and metadata all live in the
        organizer's grid, so the separate per-operation panels are gone.
        """
        self.single_pdf_panel = PageOrganizerWidget()
        self.single_pdf_panel.saved.connect(self._on_organized)
        return self.single_pdf_panel

    def _build_combine_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        self.combine_inputs = OrderedFileList("PDF Files (*.pdf)")
        out_row, self.combine_output, _ = _save_row(
            "Output PDF",
            "Save As",
            filter_text="PDF Files (*.pdf)",
        )
        # Several inputs have no single "source" field, so the default output follows the
        # first file in the list (and stops following once a path of the user's own is there).
        _link_default_output(self.combine_output, self.combine_inputs, "_combined")

        combine_button = QPushButton("Merge PDFs")
        combine_button.clicked.connect(self.run_combine)

        form.addRow("Input PDFs (in order)", self.combine_inputs)
        form.addRow(out_row)
        form.addRow(combine_button)
        return tab

    def _build_convert_tab(self) -> QWidget:
        """One conversion at a time, picked from the dropdown.

        Stacking all four conversions used to squeeze each of them (and left the image list far
        too short); the chosen pane now owns the whole panel.
        """
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        self.convert_kind = QComboBox()
        self.convert_kind.addItem("Images to PDF", "images-to-pdf")
        self.convert_kind.addItem("PDF to Text", "pdf-to-text")
        self.convert_kind.addItem("Word to PDF", "word-to-pdf")
        self.convert_kind.addItem("Excel to PDF", "excel-to-pdf")

        # Each panel below runs on a single file, or on a whole folder of them.
        self.text_conversion = ConversionPanel(
            "PDF to Text",
            input_label="Input PDF",
            input_filter="PDF Files (*.pdf)",
            pattern=DEFAULT_GLOB_PATTERN,
            button_text="Extract Text",
            output_label="Output Text",
            output_filter="Text Files (*.txt)",
            output_extension=".txt",
            with_workers=True,
        )
        self.text_conversion.run_requested.connect(self.run_pdf_to_text)

        self.word_conversion = ConversionPanel(
            "Word to PDF",
            input_label="Input Word",
            input_filter="Word Files (*.docx *.doc)",
            pattern=DEFAULT_WORD_PATTERN,
            button_text="Convert to PDF",
            output_suffix="_converted",
            with_continue_on_error=True,
        )
        self.word_conversion.run_requested.connect(self.run_word_to_pdf)

        self.excel_paper_size = QComboBox()
        for paper_name in EXCEL_PAPER_SIZES:
            self.excel_paper_size.addItem(paper_name, paper_name)
        self.excel_paper_size.setCurrentText(DEFAULT_EXCEL_PAPER_SIZE)
        self.excel_conversion = ConversionPanel(
            "Excel to PDF",
            input_label="Input Excel",
            input_filter="Excel Files (*.xlsx *.xls *.xlsm)",
            pattern=DEFAULT_EXCEL_PATTERN,
            button_text="Convert to PDF",
            output_suffix="_converted",
            with_continue_on_error=True,
        )
        self.excel_conversion.add_option("Paper Size", self.excel_paper_size)
        self.excel_conversion.run_requested.connect(self.run_excel_to_pdf)

        # The combo and the stack are filled in the same order, so the index is the choice.
        self.convert_stack = QStackedWidget()
        self.convert_stack.addWidget(self._build_images_pane())
        self.convert_stack.addWidget(self.text_conversion)
        self.convert_stack.addWidget(self.word_conversion)
        self.convert_stack.addWidget(self.excel_conversion)
        self.convert_kind.currentIndexChanged.connect(self.convert_stack.setCurrentIndex)

        selector = QFormLayout()
        selector.addRow("Conversion", self.convert_kind)
        layout.addLayout(selector)
        layout.addWidget(self.convert_stack, stretch=1)
        return tab

    def _build_images_pane(self) -> QWidget:
        """The Images to PDF pane: an ordered list of images, then where the result goes.

        The list is given the pane's spare height (and a roomy minimum), which is the space the
        dropdown frees up by showing one conversion at a time. The `Make` choice names the
        result — "Combine all images into one PDF" or "One PDF per image" — and the second one
        swaps the output row for a folder to spread them over.
        """
        pane = QGroupBox("Images to PDF")
        pane_layout = QVBoxLayout(pane)

        mode_row = QFormLayout()
        self.images_mode = QComboBox()
        # "Combine ... into one PDF" says what happens; the old "one PDF for every image" could
        # be read as one PDF *per* image, which is the other choice.
        self.images_mode.addItem("Combine all images into one PDF", "single")
        self.images_mode.addItem("One PDF per image", "per-image")
        mode_row.addRow("Make", self.images_mode)
        pane_layout.addLayout(mode_row)

        pane_layout.addWidget(QLabel("Input Images (in order)"))
        self.images_inputs = OrderedFileList("Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp)")
        self.images_inputs.list.setMinimumHeight(_MIN_CONVERT_LIST_HEIGHT)
        pane_layout.addWidget(self.images_inputs, stretch=1)

        # One combined PDF needs a filename; one PDF per image needs a folder.
        img_out_row, self.images_output, _ = _save_row(
            "Output PDF",
            "Save As",
            filter_text="PDF Files (*.pdf)",
        )
        _link_default_output(self.images_output, self.images_inputs, "")
        dir_out_row, self.images_output_dir, _ = _directory_row("Output Folder", "Select Folder")

        self.images_outputs = QStackedWidget()
        self.images_outputs.addWidget(_row_widget(img_out_row))
        self.images_outputs.addWidget(_row_widget(dir_out_row))
        pane_layout.addWidget(self.images_outputs)
        # The combo and the stack are filled in the same order, so the index is the choice.
        self.images_mode.currentIndexChanged.connect(self.images_outputs.setCurrentIndex)

        images_button = QPushButton("Create PDF")
        images_button.clicked.connect(self.run_images_to_pdf)
        pane_layout.addWidget(images_button)
        return pane

    def _build_print_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        # The same ordered list Merge uses, so the print order can be arranged beforehand.
        self.print_inputs = OrderedFileList("PDF Files (*.pdf)")
        print_button = QPushButton("Print")
        print_button.clicked.connect(self.run_print)

        form.addRow("Input PDFs (in order)", self.print_inputs)
        form.addRow(print_button)
        return tab

    def _build_batch_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        batch_box = QGroupBox("Bulk PDF Operations")
        form = QFormLayout(batch_box)

        # Action selector
        self.batch_action = QComboBox()
        self.batch_action.addItem("Rotate", "rotate")
        self.batch_action.addItem("Watermark", "watermark")
        self.batch_action.addItem("Delete Pages", "delete-pages")
        self.batch_action.addItem("Reorder", "reorder")
        self.batch_action.addItem("Export Pages", "split")

        # Common fields (always shown)
        in_row, self.batch_input_dir, _ = _directory_row("Input Folder", "Select Folder")
        out_row, self.batch_output_dir, _ = _directory_row("Output Folder", "Select Folder")

        self.batch_pattern = QLineEdit(DEFAULT_GLOB_PATTERN)
        self.batch_pattern.setToolTip("Glob pattern, supports forms like *.pdf or **/*.pdf")

        self.batch_workers = QSpinBox()
        self.batch_workers.setMinimum(MIN_WORKERS)
        self.batch_workers.setMaximum(MAX_WORKERS)
        self.batch_workers.setValue(DEFAULT_WORKERS)
        self.batch_workers.setToolTip("Number of worker threads for batch processing")

        self.batch_count_label = QLabel("")
        self.batch_count_label.setStyleSheet("color: #1a73e8;")

        # Action-specific fields using QStackedWidget
        self.batch_action_stacked = QStackedWidget()
        
        # Create empty/default widget
        empty_widget = QWidget()
        empty_layout = QVBoxLayout(empty_widget)
        empty_layout.addWidget(QLabel("Select an action to see options"))
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.batch_action_stacked.addWidget(empty_widget)
        
        # Rotate widget
        rotate_widget = QWidget()
        rotate_layout = QFormLayout(rotate_widget)
        self.batch_angle = QSpinBox()
        self.batch_angle.setMinimum(-360)
        self.batch_angle.setMaximum(360)
        self.batch_angle.setSingleStep(90)
        self.batch_angle.setValue(90)
        self.batch_pages_rotate = QLineEdit()
        self.batch_pages_rotate.setPlaceholderText("Optional: pages to rotate, e.g. 1,3,5-7")
        rotate_layout.addRow("Angle", self.batch_angle)
        rotate_layout.addRow("Pages", self.batch_pages_rotate)
        self.batch_action_stacked.addWidget(rotate_widget)
        
        # Watermark widget
        watermark_widget = QWidget()
        watermark_layout = QFormLayout(watermark_widget)
        self.batch_text = QLineEdit()
        self.batch_text.setPlaceholderText("Watermark text")
        self.batch_opacity = QDoubleSpinBox()
        self.batch_opacity.setDecimals(2)
        self.batch_opacity.setSingleStep(0.05)
        self.batch_opacity.setRange(0.0, 1.0)
        self.batch_opacity.setValue(DEFAULT_WATERMARK_OPACITY)
        self.batch_font_size = QSpinBox()
        self.batch_font_size.setMinimum(8)
        self.batch_font_size.setMaximum(300)
        self.batch_font_size.setValue(DEFAULT_WATERMARK_FONT_SIZE)
        watermark_layout.addRow("Watermark Text", self.batch_text)
        watermark_layout.addRow("Opacity", self.batch_opacity)
        watermark_layout.addRow("Font Size", self.batch_font_size)
        self.batch_action_stacked.addWidget(watermark_widget)
        
        # Delete pages widget
        delete_widget = QWidget()
        delete_layout = QFormLayout(delete_widget)
        self.batch_pages_delete = QLineEdit()
        self.batch_pages_delete.setPlaceholderText("Required: pages to delete, e.g. 2,4,6-8")
        delete_layout.addRow("Pages", self.batch_pages_delete)
        self.batch_action_stacked.addWidget(delete_widget)
        
        # Reorder widget
        reorder_widget = QWidget()
        reorder_layout = QFormLayout(reorder_widget)
        self.batch_order = QLineEdit()
        self.batch_order.setPlaceholderText("Required: full page order, e.g. 3,1,2")
        reorder_layout.addRow("Order", self.batch_order)
        self.batch_action_stacked.addWidget(reorder_widget)
        
        # Export pages widget — the same modes the Single PDF Editing panel offers, plus
        # combining every input PDF into one file (which only a folder export can do).
        export_widget = QWidget()
        export_layout = QFormLayout(export_widget)
        self.batch_export_mode = ExportModeSelector(with_combined=True)
        self.batch_export_pages = QLineEdit()
        self.batch_export_pages.setPlaceholderText("Optional: pages to export, e.g. 1,3,5-7")
        self.batch_export_prefix = QLineEdit()
        self.batch_export_prefix.setPlaceholderText("Optional filename prefix")
        export_layout.addRow("Export As", self.batch_export_mode)
        export_layout.addRow("Pages", self.batch_export_pages)
        export_layout.addRow("Prefix", self.batch_export_prefix)
        self.batch_action_stacked.addWidget(export_widget)
        self.batch_export_mode.mode_changed.connect(self._on_batch_export_mode_changed)
        self._on_batch_export_mode_changed(self.batch_export_mode.mode())

        # Run button and status
        self.run_batch_button = QPushButton("Run Bulk Operation")
        self.run_batch_button.clicked.connect(self.run_batch_operation)
        self.dry_run_button = QPushButton("Dry Run (preview matching files)")
        self.dry_run_button.clicked.connect(self.run_batch_dry_run)

        self.batch_status = QLabel("Idle")
        self.batch_progress = QProgressBar()
        self.batch_progress.setRange(0, 1)
        self.batch_progress.setValue(0)

        self.batch_log = QPlainTextEdit()
        self.batch_log.setReadOnly(True)
        self.batch_log.setPlaceholderText("Bulk progress and errors will appear here.")
        self.batch_log.document().setMaximumBlockCount(2000)

        # Connect action change and the live file-count preview
        self.batch_action.currentIndexChanged.connect(self._on_batch_action_changed)
        self.batch_input_dir.textChanged.connect(self._update_batch_count)
        self.batch_pattern.textChanged.connect(self._update_batch_count)

        # Add to form
        form.addRow("Action", self.batch_action)
        form.addRow(in_row)
        form.addRow(out_row)
        form.addRow("Pattern", self.batch_pattern)
        form.addRow("", self.batch_count_label)
        form.addRow("Workers", self.batch_workers)
        form.addRow("Options", self.batch_action_stacked)
        form.addRow(self.run_batch_button)
        form.addRow(self.dry_run_button)
        form.addRow("Status", self.batch_status)
        form.addRow("Progress", self.batch_progress)

        layout.addWidget(batch_box)
        layout.addWidget(self.batch_log)
        layout.addStretch(1)

        # Initialize field visibility
        self._on_batch_action_changed()
        return tab

    def _update_batch_count(self) -> None:
        """Show how many files currently match the input folder + pattern."""
        directory = self.batch_input_dir.text().strip()
        pattern = self.batch_pattern.text().strip() or DEFAULT_GLOB_PATTERN
        if not directory:
            self.batch_count_label.setText("")
            return
        path = Path(directory).expanduser()
        if not path.is_dir():
            self.batch_count_label.setText("")
            return
        try:
            count = sum(1 for candidate in path.glob(pattern) if candidate.is_file())
        except (OSError, ValueError):
            self.batch_count_label.setText("")
            return
        self.batch_count_label.setText(f"{count} file(s) match '{pattern}'")

    def _on_batch_action_changed(self) -> None:
        """Update displayed fields based on selected bulk action."""
        action = self.batch_action.currentData()

        # Stack pages are added in the same order as the combo items (after the leading
        # "empty" page), so the index is derived rather than duplicated in a table.
        self.batch_action_stacked.setCurrentIndex(self.batch_action.currentIndex() + 1)

        # Every bulk action works on PDFs and writes its results into the output folder.
        self.batch_output_dir.setEnabled(action in OUTPUT_DIR_ACTIONS)

    def _on_batch_export_mode_changed(self, mode: str) -> None:
        """Update the fields that only some export modes use.

        A one-PDF-per-file export is named after each input file (like every other bulk
        operation), so there is no prefix to type; the combined mode uses it to name its one
        output, and the page-file modes use it to name each page file.
        """
        if mode == ExportModeSelector.SINGLE:
            tooltip = "Not used when one PDF per input file is exported"
        elif mode == ExportModeSelector.COMBINED:
            tooltip = "Name of the combined PDF, e.g. report_selected.pdf"
        else:
            tooltip = (
                "Prefix for the exported page files; the source name is kept as well when "
                "several PDFs are exported together"
            )
        self.batch_export_prefix.setEnabled(mode != ExportModeSelector.SINGLE)
        self.batch_export_prefix.setToolTip(tooltip)

    def _show_success(self, message: str) -> None:
        # Non-modal: report in the status bar rather than a blocking dialog.
        self.status.showMessage(message, 10000)
        logger.info(message)

    def _show_error(self, error: Exception) -> None:
        QMessageBox.critical(self, "Error", f"{error}\n\nSee the log for details.")

    def run_combine(self) -> None:
        try:
            inputs = self.combine_inputs.paths()
            if not inputs:
                raise ValueError("At least one input PDF is required.")
            for file_path in inputs:
                if not file_path.exists() or not file_path.is_file():
                    raise FileNotFoundError(f"Input PDF not found: {file_path}")
                if not self.combine_inputs.accepts(str(file_path)):
                    raise ValueError(f"Not a PDF file: {file_path.name}")
            requested = _output_file_from_input(self.combine_output.text(), "Output PDF")
            output_file = _unique_path(requested)
            self._run_async(lambda: combine_pdfs(inputs, output_file), "Combine complete.")
        except Exception as exc:  # noqa: BLE001
            self._show_error(exc)

    def run_images_to_pdf(self) -> None:
        """Combine the listed images into one PDF, or write one PDF per image."""
        try:
            inputs = self.images_inputs.paths()
            if not inputs:
                raise ValueError("At least one input image is required.")
            for file_path in inputs:
                if not file_path.exists() or not file_path.is_file():
                    raise FileNotFoundError(f"Input image not found: {file_path}")
                if not self.images_inputs.accepts(str(file_path)):
                    raise ValueError(f"Not an image file: {file_path.name}")

            if self.images_mode.currentData() == "per-image":
                targets = [
                    (source, _unique_path(target))
                    for source, target in _image_split_targets(
                        inputs,
                        _output_dir_from_input(self.images_output_dir.text(), "Output Folder"),
                    )
                ]

                def write_each() -> str:
                    for source, target in targets:
                        images_to_pdf([source], target)
                    return f"Created {len(targets)} PDF(s), one per image."

                self._run_async(write_each, "Conversion complete.")
                return

            requested = _output_file_from_input(self.images_output.text(), "Output PDF")
            output_file = _unique_path(requested)
            self._run_async(lambda: images_to_pdf(inputs, output_file), "Conversion complete.")
        except Exception as exc:  # noqa: BLE001
            self._show_error(exc)

    def run_pdf_to_text(self) -> None:
        """Extract text from one PDF, or from every matching PDF in a folder."""
        self._run_conversion(
            self.text_conversion,
            "Input PDF",
            "Text extraction complete.",
            lambda src, dst: pdf_to_text(src, dst),
            self._bulk_pdf_to_text,
        )

    def run_word_to_pdf(self) -> None:
        """Convert one Word document, or a whole folder of them, to PDF."""
        self._run_conversion(
            self.word_conversion,
            "Input Word",
            "Word to PDF conversion complete.",
            lambda src, dst: word_to_pdf(src, dst),
            self._bulk_word_to_pdf,
        )

    def run_excel_to_pdf(self) -> None:
        """Convert one Excel workbook, or a whole folder of them, to PDF."""
        paper_size = self.excel_paper_size.currentText()
        self._run_conversion(
            self.excel_conversion,
            "Input Excel",
            "Excel to PDF conversion complete.",
            lambda src, dst: excel_to_pdf(src, dst, paper_size),
            self._bulk_excel_to_pdf,
        )

    def _run_conversion(
        self,
        panel: ConversionPanel,
        input_label: str,
        success_message: str,
        single: Callable[[Path, Path], None],
        bulk: Callable[[Path, Path, str, int, bool], str],
    ) -> None:
        """Run one Convert-panel action, on a single file or on a whole folder of them."""
        try:
            if panel.is_bulk():
                input_dir = _existing_dir_from_input(panel.input_dir.text(), "Input Folder")
                output_dir = _output_dir_from_input(panel.output_dir.text(), "Output Folder")
                pattern = panel.pattern.text().strip() or DEFAULT_GLOB_PATTERN
                workers = panel.workers.value()
                continue_on_error = panel.continue_on_error.isChecked()
                self._run_async(
                    lambda: bulk(input_dir, output_dir, pattern, workers, continue_on_error),
                    success_message,
                )
                return
            input_file = _existing_file_from_input(panel.input_edit.text(), input_label)
            requested = _output_file_from_input(panel.output_edit.text(), "Output")
            output_file = _unique_path(requested)
            self._run_async(lambda: single(input_file, output_file), success_message)
        except Exception as exc:  # noqa: BLE001
            self._show_error(exc)

    @staticmethod
    def _bulk_pdf_to_text(
        input_dir: Path,
        output_dir: Path,
        pattern: str,
        workers: int,
        _continue_on_error: bool,
    ) -> str:
        written = batch_pdf_to_text(input_dir, output_dir, pattern, workers, unique_names=True)
        return f"Extracted text from {len(written)} PDF(s)."

    @staticmethod
    def _bulk_word_to_pdf(
        input_dir: Path,
        output_dir: Path,
        pattern: str,
        _workers: int,
        continue_on_error: bool,
    ) -> str:
        # Word/Excel go through COM, which is single-threaded whatever the worker count.
        written = batch_word_to_pdf(
            input_dir, output_dir, pattern, 1, None, continue_on_error, unique_names=True
        )
        return f"Converted {len(written)} Word file(s) to PDF."

    def _bulk_excel_to_pdf(
        self,
        input_dir: Path,
        output_dir: Path,
        pattern: str,
        _workers: int,
        continue_on_error: bool,
    ) -> str:
        written = batch_excel_to_pdf(
            input_dir,
            output_dir,
            pattern,
            1,
            None,
            continue_on_error,
            None,
            self.excel_paper_size.currentText(),
            unique_names=True,
        )
        return f"Converted {len(written)} Excel file(s) to PDF."

    def run_print(self) -> None:
        try:
            inputs = self.print_inputs.paths()
            if not inputs:
                raise ValueError("At least one input PDF is required.")
            for file_path in inputs:
                if not file_path.exists() or not file_path.is_file():
                    raise FileNotFoundError(f"Input PDF not found: {file_path}")
                if not self.print_inputs.accepts(str(file_path)):
                    raise ValueError(f"Not a PDF file: {file_path.name}")
            self._run_async(lambda: _print_in_order(inputs), "Print command submitted.")
        except Exception as exc:  # noqa: BLE001
            self._show_error(exc)

    def run_batch_operation(self) -> None:
        try:
            if self.batch_thread is not None and self.batch_thread.isRunning():
                raise ValueError("A batch operation is already running. Please wait for it to complete.")

            action = str(self.batch_action.currentData())
            input_dir = _existing_dir_from_input(self.batch_input_dir.text(), "Input Folder")
            pattern = self.batch_pattern.text().strip() or "*.pdf"
            workers = self.batch_workers.value()

            logger.info(f"Starting batch operation: {action} in {input_dir}")

            # Validate output directory requirement before starting
            output_dir: Path | None = None
            if action in OUTPUT_DIR_ACTIONS:
                output_dir = _output_dir_from_input(self.batch_output_dir.text(), "Output Folder")

            # Get action-specific parameters based on which widget is shown
            pages: str | None = None
            order: str | None = None
            text: str | None = None
            prefix: str | None = None
            opacity: float = DEFAULT_WATERMARK_OPACITY
            paper_size: str | None = None
            export_mode: str = ExportModeSelector.PER_PAGE
            chunk_size: int | None = None

            if action == "rotate":
                pages = self.batch_pages_rotate.text().strip() or None
            elif action == "watermark":
                text = self.batch_text.text().strip() or None
                opacity = self.batch_opacity.value()
            elif action == "delete-pages":
                pages = self.batch_pages_delete.text().strip() or None
            elif action == "reorder":
                order = self.batch_order.text().strip() or None
            elif action == "split":
                pages = self.batch_export_pages.text().strip() or None
                prefix = self.batch_export_prefix.text().strip() or None
                export_mode = self.batch_export_mode.mode()
                chunk_size = (
                    self.batch_export_mode.chunk_size()
                    if export_mode == ExportModeSelector.CHUNKED
                    else None
                )

            # Validate action-specific parameters before starting thread
            if action == "watermark" and not text:
                raise ValueError("Watermark text is required.")
            if action == "watermark" and (opacity < 0.0 or opacity > 1.0):
                raise ValueError("Opacity must be between 0.0 and 1.0.")
            if action == "delete-pages" and not pages:
                raise ValueError("Pages to delete are required.")
            if action == "reorder" and not order:
                raise ValueError("Page order is required.")
            if action == "rotate":
                # Validate angle for rotate
                angle = self.batch_angle.value()
                if angle % 90 != 0:
                    raise ValueError("Rotation angle must be a multiple of 90.")

            self.batch_log.clear()
            self.batch_progress.setRange(0, 0)
            self.batch_status.setText("Starting...")
            self._set_busy(True)

            self.batch_worker = BatchWorker(
                action,
                input_dir,
                output_dir,
                pattern,
                workers,
                self.batch_angle.value(),
                pages,
                order,
                text,
                opacity,
                self.batch_font_size.value(),
                prefix,
                False,
                paper_size,
                export_mode=export_mode,
                chunk_size=chunk_size,
            )

            self.batch_thread = QThread(self)
            self.batch_worker.moveToThread(self.batch_thread)

            self.batch_thread.started.connect(self.batch_worker.run)
            self.batch_worker.progress.connect(self._on_batch_progress)
            self.batch_worker.log.connect(self._append_batch_log)
            self.batch_worker.finished.connect(self._on_batch_finished)
            self.batch_worker.failed.connect(self._on_batch_failed)

            self.batch_worker.finished.connect(self.batch_thread.quit)
            self.batch_worker.failed.connect(self.batch_thread.quit)
            self.batch_thread.finished.connect(self._cleanup_batch_thread)

            self.batch_thread.start()

        except Exception as exc:  # noqa: BLE001
            self._show_error(exc)

    def run_batch_dry_run(self) -> None:
        """List the files a batch would process without writing anything."""
        action = str(self.batch_action.currentData())
        directory = self.batch_input_dir.text().strip()
        pattern = self.batch_pattern.text().strip() or DEFAULT_GLOB_PATTERN
        self.batch_log.clear()
        if not directory:
            self.batch_log.appendPlainText("No input folder selected.")
            return
        path = Path(directory).expanduser()
        if not path.is_dir():
            self.batch_log.appendPlainText(f"Input folder not found: {path}")
            return
        try:
            files = sorted(candidate for candidate in path.glob(pattern) if candidate.is_file())
        except (OSError, ValueError) as exc:
            self.batch_log.appendPlainText(f"Invalid pattern: {exc}")
            return
        self.batch_log.appendPlainText(f"[dry-run] Action: {action}")
        self.batch_log.appendPlainText(f"[dry-run] Pattern: {pattern}")
        self.batch_log.appendPlainText(f"[dry-run] {len(files)} file(s) would be processed:")
        for file_path in files[:50]:
            self.batch_log.appendPlainText(f"  - {file_path.name}")
        if len(files) > 50:
            self.batch_log.appendPlainText(f"  ... and {len(files) - 50} more")
        self.status.showMessage("Dry run complete (no files written).", 8000)

    def _run_async(self, operation: Callable[[], str | None], success_message: str) -> None:
        """Run a single-file operation off the GUI thread so the window stays responsive."""
        if self._single_thread is not None and self._single_thread.isRunning():
            self._show_error(ValueError("An operation is already running. Please wait."))
            return
        self._single_thread = QThread(self)
        self._single_worker = SingleOpWorker(operation, success_message)
        self._single_worker.moveToThread(self._single_thread)

        self._single_thread.started.connect(self._single_worker.run)
        self._single_worker.finished.connect(self._on_single_finished)
        self._single_worker.failed.connect(self._on_single_failed)
        self._single_worker.finished.connect(self._single_thread.quit)
        self._single_worker.failed.connect(self._single_thread.quit)
        self._single_thread.finished.connect(self._cleanup_single_thread)

        self._set_busy(True)
        self._single_thread.start()

    def _on_single_finished(self, message: str) -> None:
        self._show_success(message)

    def _on_single_failed(self, message: str) -> None:
        logger.error("Operation failed: %s", message)
        self._show_error(Exception(message))

    def _cleanup_single_thread(self) -> None:
        self._set_busy(False)
        if self._single_worker is not None:
            self._single_worker.deleteLater()
        if self._single_thread is not None:
            self._single_thread.deleteLater()
        self._single_worker = None
        self._single_thread = None

    def _append_batch_log(self, message: str) -> None:
        self.batch_log.appendPlainText(message)

    def _on_batch_progress(self, current: int, total: int, status: str) -> None:
        if total <= 0:
            self.batch_progress.setRange(0, 0)
            self.progress.setRange(0, 0)
        else:
            self.batch_progress.setRange(0, total)
            self.batch_progress.setValue(min(current, total))
            self.progress.setRange(0, total)
            self.progress.setValue(min(current, total))
        self.batch_status.setText(status)
        self.status.showMessage(status)

    def _on_batch_finished(self, message: str) -> None:
        if self.batch_progress.maximum() == 0:
            self.batch_progress.setRange(0, 1)
            self.batch_progress.setValue(1)
        self.batch_status.setText("Completed")
        self._append_batch_log(message)
        self._show_success(message)

    def _on_batch_failed(self, message: str) -> None:
        if self.batch_progress.maximum() == 0:
            self.batch_progress.setRange(0, 1)
            self.batch_progress.setValue(0)
        self.batch_status.setText("Failed")
        self._append_batch_log(f"[FATAL] {message}")
        self._show_error(Exception(message))

    def _cleanup_batch_thread(self) -> None:
        self._set_busy(False)
        if self.batch_worker is not None:
            self.batch_worker.deleteLater()
        if self.batch_thread is not None:
            self.batch_thread.deleteLater()
        self.batch_worker = None
        self.batch_thread = None


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    app = QApplication(sys.argv)
    apply_theme(app)
    window = MainWindow()
    window.showMaximized()
    return app.exec()
