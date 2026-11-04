"""Main application window for the PySide6 GUI."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QSettings, Qt, QThread
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core import (
    DEFAULT_EXCEL_PAPER_SIZE,
    DEFAULT_GLOB_PATTERN,
    DEFAULT_WATERMARK_FONT_SIZE,
    DEFAULT_WATERMARK_OPACITY,
    DEFAULT_WORKERS,
    EXCEL_PAPER_SIZES,
    MAX_WORKERS,
    MIN_WORKERS,
    OFFICE_ACTIONS,
    OUTPUT_DIR_ACTIONS,
    combine_pdfs,
    delete_page_range,
    extract_pages,
    get_metadata,
    images_to_pdf,
    insert_pages,
    pdf_to_text,
    print_pdf,
    reorder_pages,
    rotate_pdf,
    set_metadata,
    split_pdf,
    watermark_pdf,
)
from .theme import apply_theme
from .widgets import (
    OrderedFileList,
    PageRangeField,
    _directory_row,
    _existing_dir_from_input,
    _existing_file_from_input,
    _file_row,
    _output_dir_from_input,
    _output_file_from_input,
    _paths_from_input,
    _save_row,
)
from .workers import BatchWorker, SingleOpWorker

logger = logging.getLogger(__name__)


def _scrollable(widget: QWidget) -> QScrollArea:
    """Wrap a tab's content so tall panels stay scrollable."""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(widget)
    return area


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

        hint = QLabel("Tip: You can drag and drop files/folders into most path fields.")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: #666666;")
        root_layout.addWidget(hint)

        tabs = QTabWidget()
        tabs.addTab(_scrollable(self._build_edit_tab()), "Edit")
        tabs.addTab(_scrollable(self._build_split_tab()), "Split")
        tabs.addTab(_scrollable(self._build_combine_tab()), "Combine")
        tabs.addTab(_scrollable(self._build_convert_tab()), "Convert")
        tabs.addTab(_scrollable(self._build_batch_tab()), "Batch")
        tabs.addTab(_scrollable(self._build_print_tab()), "Print")
        root_layout.addWidget(tabs, stretch=1)
        self.tabs = tabs

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
        self.status.showMessage("Ready")

        self.batch_thread: QThread | None = None
        self.batch_worker: BatchWorker | None = None
        self._single_thread: QThread | None = None
        self._single_worker: SingleOpWorker | None = None

    def _set_busy(self, busy: bool) -> None:
        """Toggle interaction and the progress/cancel affordances while work runs."""
        self.tabs.setEnabled(not busy)
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
        super().closeEvent(event)

    def _bind_page_count(self, field: PageRangeField, input_edit: QLineEdit) -> None:
        """Keep a PageRangeField's page-count hint in sync with the input PDF."""

        def refresh(*_args: object) -> None:
            text = input_edit.text().strip().split(";")[0].strip()
            if not text:
                return
            try:
                from pypdf import PdfReader

                count = len(PdfReader(str(Path(text).expanduser())).pages)
            except Exception:  # noqa: BLE001
                return
            field.set_page_count(count)

        input_edit.textChanged.connect(refresh)

    def _build_edit_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        rotate_box = QGroupBox("Rotate")
        rotate_form = QFormLayout(rotate_box)

        rotate_in_row, self.rotate_input, _ = _file_row("Input PDF", "Browse", filter_text="PDF Files (*.pdf)")
        rotate_out_row, self.rotate_output, _ = _save_row(
            "Output PDF",
            "Save As",
            filter_text="PDF Files (*.pdf)",
            source_edit=self.rotate_input,
            suffix="_rotated",
        )

        self.rotate_angle = QSpinBox()
        self.rotate_angle.setMinimum(-360)
        self.rotate_angle.setMaximum(360)
        self.rotate_angle.setSingleStep(90)
        self.rotate_angle.setValue(90)

        self.rotate_pages = PageRangeField("Optional pages to rotate, e.g. 1,3,5-7")

        rotate_button = QPushButton("Rotate")
        rotate_button.clicked.connect(self.run_rotate)

        rotate_form.addRow(rotate_in_row)
        rotate_form.addRow(rotate_out_row)
        rotate_form.addRow("Angle", self.rotate_angle)
        rotate_form.addRow("Pages", self.rotate_pages)
        rotate_form.addRow(rotate_button)

        watermark_box = QGroupBox("Watermark")
        watermark_form = QFormLayout(watermark_box)

        water_in_row, self.watermark_input, _ = _file_row(
            "Input PDF", "Browse", filter_text="PDF Files (*.pdf)"
        )
        water_out_row, self.watermark_output, _ = _save_row(
            "Output PDF",
            "Save As",
            filter_text="PDF Files (*.pdf)",
            source_edit=self.watermark_input,
            suffix="_watermarked",
        )

        self.watermark_text = QLineEdit()
        self.watermark_text.setPlaceholderText("CONFIDENTIAL")

        self.watermark_opacity = QDoubleSpinBox()
        self.watermark_opacity.setDecimals(2)
        self.watermark_opacity.setSingleStep(0.05)
        self.watermark_opacity.setRange(0.0, 1.0)
        self.watermark_opacity.setValue(DEFAULT_WATERMARK_OPACITY)
        self.watermark_size = QSpinBox()
        self.watermark_size.setMinimum(8)
        self.watermark_size.setMaximum(300)
        self.watermark_size.setValue(DEFAULT_WATERMARK_FONT_SIZE)

        watermark_button = QPushButton("Apply Watermark")
        watermark_button.clicked.connect(self.run_watermark)

        watermark_form.addRow(water_in_row)
        watermark_form.addRow(water_out_row)
        watermark_form.addRow("Text", self.watermark_text)
        watermark_form.addRow("Opacity (0.0-1.0)", self.watermark_opacity)
        watermark_form.addRow("Font Size", self.watermark_size)
        watermark_form.addRow(watermark_button)

        delete_box = QGroupBox("Delete Pages")
        delete_form = QFormLayout(delete_box)

        delete_in_row, self.delete_input, _ = _file_row("Input PDF", "Browse", filter_text="PDF Files (*.pdf)")
        delete_out_row, self.delete_output, _ = _save_row(
            "Output PDF",
            "Save As",
            filter_text="PDF Files (*.pdf)",
            source_edit=self.delete_input,
            suffix="_deleted",
        )
        self.delete_pages = PageRangeField("Pages to delete, e.g. 2,4,6-8")

        delete_button = QPushButton("Delete Pages")
        delete_button.clicked.connect(self.run_delete_pages)

        delete_form.addRow(delete_in_row)
        delete_form.addRow(delete_out_row)
        delete_form.addRow("Pages", self.delete_pages)
        delete_form.addRow(delete_button)

        reorder_box = QGroupBox("Reorder Pages")
        reorder_form = QFormLayout(reorder_box)

        reorder_in_row, self.reorder_input, _ = _file_row("Input PDF", "Browse", filter_text="PDF Files (*.pdf)")
        reorder_out_row, self.reorder_output, _ = _save_row(
            "Output PDF",
            "Save As",
            filter_text="PDF Files (*.pdf)",
            source_edit=self.reorder_input,
            suffix="_reordered",
        )
        self.reorder_order = QLineEdit()
        self.reorder_order.setPlaceholderText("Required full order, e.g. 3,1,2")

        reorder_button = QPushButton("Reorder")
        reorder_button.clicked.connect(self.run_reorder)

        reorder_form.addRow(reorder_in_row)
        reorder_form.addRow(reorder_out_row)
        reorder_form.addRow("Order", self.reorder_order)
        reorder_form.addRow(reorder_button)

        extract_box = QGroupBox("Extract Pages")
        extract_form = QFormLayout(extract_box)
        extract_in_row, self.extract_input, _ = _file_row(
            "Input PDF", "Browse", filter_text="PDF Files (*.pdf)"
        )
        extract_out_row, self.extract_output, _ = _save_row(
            "Output PDF",
            "Save As",
            filter_text="PDF Files (*.pdf)",
            source_edit=self.extract_input,
            suffix="_extracted",
        )
        self.extract_pages = PageRangeField("Pages to extract, e.g. 1,3,5-7")
        extract_button = QPushButton("Extract to New PDF")
        extract_button.clicked.connect(self.run_extract_pages)
        extract_form.addRow(extract_in_row)
        extract_form.addRow(extract_out_row)
        extract_form.addRow("Pages", self.extract_pages)
        extract_form.addRow(extract_button)

        insert_box = QGroupBox("Insert Pages")
        insert_form = QFormLayout(insert_box)
        insert_in_row, self.insert_input, _ = _file_row(
            "Base PDF", "Browse", filter_text="PDF Files (*.pdf)"
        )
        insert_src_row, self.insert_file, _ = _file_row(
            "Insert PDF", "Browse", filter_text="PDF Files (*.pdf)"
        )
        insert_out_row, self.insert_output, _ = _save_row(
            "Output PDF",
            "Save As",
            filter_text="PDF Files (*.pdf)",
            source_edit=self.insert_input,
            suffix="_inserted",
        )
        self.insert_at = QSpinBox()
        self.insert_at.setMinimum(1)
        self.insert_at.setMaximum(100000)
        self.insert_at.setValue(1)
        self.insert_pages_field = PageRangeField("Optional pages from the inserted PDF")
        insert_button = QPushButton("Insert Pages")
        insert_button.clicked.connect(self.run_insert_pages)
        insert_form.addRow(insert_in_row)
        insert_form.addRow(insert_src_row)
        insert_form.addRow(insert_out_row)
        insert_form.addRow("Insert before page", self.insert_at)
        insert_form.addRow("Pages", self.insert_pages_field)
        insert_form.addRow(insert_button)

        meta_box = QGroupBox("Metadata")
        meta_form = QFormLayout(meta_box)
        meta_in_row, self.meta_input, _ = _file_row(
            "Input PDF", "Browse", filter_text="PDF Files (*.pdf)"
        )
        meta_out_row, self.meta_output, _ = _save_row(
            "Output PDF",
            "Save As",
            filter_text="PDF Files (*.pdf)",
            source_edit=self.meta_input,
            suffix="_tagged",
        )
        self.meta_title = QLineEdit()
        self.meta_author = QLineEdit()
        self.meta_subject = QLineEdit()
        self.meta_keywords = QLineEdit()
        meta_buttons = QHBoxLayout()
        meta_show_button = QPushButton("Show current metadata")
        meta_show_button.clicked.connect(self.run_show_metadata)
        meta_save_button = QPushButton("Save metadata")
        meta_save_button.clicked.connect(self.run_save_metadata)
        meta_buttons.addWidget(meta_show_button)
        meta_buttons.addWidget(meta_save_button)
        meta_form.addRow(meta_in_row)
        meta_form.addRow(meta_out_row)
        meta_form.addRow("Title", self.meta_title)
        meta_form.addRow("Author", self.meta_author)
        meta_form.addRow("Subject", self.meta_subject)
        meta_form.addRow("Keywords", self.meta_keywords)
        meta_form.addRow(meta_buttons)

        layout.addWidget(rotate_box)
        layout.addWidget(watermark_box)
        layout.addWidget(delete_box)
        layout.addWidget(reorder_box)
        layout.addWidget(extract_box)
        layout.addWidget(insert_box)
        layout.addWidget(meta_box)
        layout.addStretch(1)
        self._bind_page_count(self.rotate_pages, self.rotate_input)
        self._bind_page_count(self.delete_pages, self.delete_input)
        self._bind_page_count(self.extract_pages, self.extract_input)
        self._bind_page_count(self.insert_pages_field, self.insert_file)
        return tab

    def _build_split_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        split_in_row, self.split_input, _ = _file_row("Input PDF", "Browse", filter_text="PDF Files (*.pdf)")
        split_out_row, self.split_output_dir, _ = _directory_row("Output Folder", "Select Folder")
        self.split_pages = PageRangeField("Optional pages, e.g. 1,3,5-7")
        self.split_prefix = QLineEdit()
        self.split_prefix.setPlaceholderText("Optional filename prefix")

        split_button = QPushButton("Split into Pages")
        split_button.clicked.connect(self.run_split)

        form.addRow(split_in_row)
        form.addRow(split_out_row)
        form.addRow("Pages", self.split_pages)
        form.addRow("Prefix", self.split_prefix)
        form.addRow(split_button)
        self._bind_page_count(self.split_pages, self.split_input)
        return tab

    def _build_combine_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        self.combine_inputs = OrderedFileList("PDF Files (*.pdf)")
        out_row, self.combine_output, _ = _save_row(
            "Output PDF",
            "Save As",
            filter_text="PDF Files (*.pdf)",
            suffix="_combined",
        )

        combine_button = QPushButton("Merge PDFs")
        combine_button.clicked.connect(self.run_combine)

        form.addRow("Input PDFs (in order)", self.combine_inputs)
        form.addRow(out_row)
        form.addRow(combine_button)
        return tab

    def _build_convert_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        images_box = QGroupBox("Images to PDF")
        images_form = QFormLayout(images_box)

        self.images_inputs = OrderedFileList("Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp)")
        img_out_row, self.images_output, _ = _save_row(
            "Output PDF",
            "Save As",
            filter_text="PDF Files (*.pdf)",
        )

        images_button = QPushButton("Create PDF")
        images_button.clicked.connect(self.run_images_to_pdf)

        images_form.addRow("Input Images (in order)", self.images_inputs)
        images_form.addRow(img_out_row)
        images_form.addRow(images_button)

        text_box = QGroupBox("PDF to Text")
        text_form = QFormLayout(text_box)

        text_in_row, self.text_input, _ = _file_row("Input PDF", "Browse", filter_text="PDF Files (*.pdf)")
        text_out_row, self.text_output, _ = _save_row(
            "Output Text",
            "Save As",
            filter_text="Text Files (*.txt)",
            source_edit=self.text_input,
            extension=".txt",
        )

        text_button = QPushButton("Extract Text")
        text_button.clicked.connect(self.run_pdf_to_text)

        text_form.addRow(text_in_row)
        text_form.addRow(text_out_row)
        text_form.addRow(text_button)

        layout.addWidget(images_box)
        layout.addWidget(text_box)
        layout.addStretch(1)
        return tab

    def _build_print_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        in_row, self.print_input, _ = _file_row("Input PDF", "Browse", filter_text="PDF Files (*.pdf)")
        print_button = QPushButton("Print")
        print_button.clicked.connect(self.run_print)

        form.addRow(in_row)
        form.addRow(print_button)
        return tab

    def _build_batch_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        batch_box = QGroupBox("Batch PDF Operations")
        form = QFormLayout(batch_box)

        # Action selector
        self.batch_action = QComboBox()
        self.batch_action.addItem("Rotate", "rotate")
        self.batch_action.addItem("Watermark", "watermark")
        self.batch_action.addItem("Delete Pages", "delete-pages")
        self.batch_action.addItem("Reorder", "reorder")
        self.batch_action.addItem("Split", "split")
        self.batch_action.addItem("PDF to Text", "pdf-to-text")
        self.batch_action.addItem("Word to PDF", "word-to-pdf")
        self.batch_action.addItem("Excel to PDF", "excel-to-pdf")
        self.batch_action.addItem("Print", "print")

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

        self.batch_continue_on_error = QCheckBox("Continue on error (Office conversions)")
        self.batch_continue_on_error.setChecked(True)

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
        
        # Split widget
        split_widget = QWidget()
        split_layout = QFormLayout(split_widget)
        self.batch_pages_split = QLineEdit()
        self.batch_pages_split.setPlaceholderText("Optional: pages to extract, e.g. 1,3,5-7")
        self.batch_prefix = QLineEdit()
        self.batch_prefix.setPlaceholderText("Optional filename prefix")
        split_layout.addRow("Pages", self.batch_pages_split)
        split_layout.addRow("Split Prefix", self.batch_prefix)
        self.batch_action_stacked.addWidget(split_widget)
        
        # PDF to Text widget (no extra fields)
        text_widget = QWidget()
        text_layout = QVBoxLayout(text_widget)
        text_layout.addWidget(QLabel("No additional options"))
        text_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.batch_action_stacked.addWidget(text_widget)
        
        # Word to PDF widget (no extra fields)
        word_widget = QWidget()
        word_layout = QVBoxLayout(word_widget)
        word_layout.addWidget(QLabel("No additional options"))
        word_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.batch_action_stacked.addWidget(word_widget)
        
        # Excel to PDF widget (with paper size option)
        excel_widget = QWidget()
        excel_layout = QFormLayout(excel_widget)
        self.batch_excel_paper_size = QComboBox()
        for paper_name in EXCEL_PAPER_SIZES.keys():
            self.batch_excel_paper_size.addItem(paper_name, paper_name)
        # Set default to A4
        default_index = self.batch_excel_paper_size.findText(DEFAULT_EXCEL_PAPER_SIZE)
        if default_index >= 0:
            self.batch_excel_paper_size.setCurrentIndex(default_index)
        excel_layout.addRow("Paper Size", self.batch_excel_paper_size)
        self.batch_action_stacked.addWidget(excel_widget)
        
        # Print widget (no extra fields)
        print_widget = QWidget()
        print_layout = QVBoxLayout(print_widget)
        print_layout.addWidget(QLabel("No additional options"))
        print_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.batch_action_stacked.addWidget(print_widget)

        # Run button and status
        self.run_batch_button = QPushButton("Run Batch Operation")
        self.run_batch_button.clicked.connect(self.run_batch_operation)
        self.dry_run_button = QPushButton("Dry Run (preview matching files)")
        self.dry_run_button.clicked.connect(self.run_batch_dry_run)

        self.batch_status = QLabel("Idle")
        self.batch_progress = QProgressBar()
        self.batch_progress.setRange(0, 1)
        self.batch_progress.setValue(0)

        self.batch_log = QPlainTextEdit()
        self.batch_log.setReadOnly(True)
        self.batch_log.setPlaceholderText("Batch progress and errors will appear here.")
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
        form.addRow(self.batch_continue_on_error)
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
        """Update displayed fields based on selected batch action."""
        action = self.batch_action.currentData()
        
        # Stack pages are added in the same order as the combo items (after the leading
        # "empty" page), so the index is derived rather than duplicated in a table.
        
        self.batch_action_stacked.setCurrentIndex(self.batch_action.currentIndex() + 1)
        
        # Update pattern based on action
        if action == "word-to-pdf":
            self.batch_pattern.setText("*.docx")
            self.batch_pattern.setToolTip("Glob pattern for Word files")
        elif action == "excel-to-pdf":
            self.batch_pattern.setText("*.xlsx")
            self.batch_pattern.setToolTip("Glob pattern for Excel files")
        else:
            self.batch_pattern.setText(DEFAULT_GLOB_PATTERN)
            self.batch_pattern.setToolTip("Glob pattern, supports forms like *.pdf or **/*.pdf")
        
        # Update output directory requirement
        self.batch_output_dir.setEnabled(action in OUTPUT_DIR_ACTIONS)

        # Handle Office-specific settings
        office_action = action in OFFICE_ACTIONS
        if office_action and self.batch_workers.value() != 1:
            self.batch_workers.setValue(1)
        self.batch_workers.setEnabled(not office_action)
        self.batch_continue_on_error.setEnabled(office_action)

    def _show_success(self, message: str) -> None:
        # Non-modal: report in the status bar rather than a blocking dialog.
        self.status.showMessage(message, 10000)
        logger.info(message)

    def _show_error(self, error: Exception) -> None:
        QMessageBox.critical(self, "Error", str(error))

    def run_rotate(self) -> None:
        try:
            input_file = _existing_file_from_input(self.rotate_input.text(), "Input PDF")
            output_file = _output_file_from_input(self.rotate_output.text(), "Output PDF")
            pages = self.rotate_pages.value()
            angle = self.rotate_angle.value()
            logger.info("Starting rotation: %s -> %s", input_file, output_file)
            self._run_async(
                lambda: rotate_pdf(input_file, output_file, angle, pages),
                "Rotation complete.",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Rotation failed: %s", exc, exc_info=True)
            self._show_error(exc)

    def run_watermark(self) -> None:
        try:
            input_file = _existing_file_from_input(self.watermark_input.text(), "Input PDF")
            output_file = _output_file_from_input(self.watermark_output.text(), "Output PDF")
            text = self.watermark_text.text().strip()
            if not text:
                raise ValueError("Watermark text is required.")
            opacity = self.watermark_opacity.value()
            font_size = self.watermark_size.value()
            logger.info("Starting watermark: %s -> %s, text='%s'", input_file, output_file, text)
            self._run_async(
                lambda: watermark_pdf(input_file, output_file, text, opacity, font_size),
                "Watermark complete.",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Watermark failed: %s", exc, exc_info=True)
            self._show_error(exc)

    def run_combine(self) -> None:
        try:
            inputs = self.combine_inputs.paths()
            if not inputs:
                raise ValueError("At least one input PDF is required.")
            for file_path in inputs:
                if not file_path.exists() or not file_path.is_file():
                    raise FileNotFoundError(f"Input PDF not found: {file_path}")
            output_file = _output_file_from_input(self.combine_output.text(), "Output PDF")
            self._run_async(lambda: combine_pdfs(inputs, output_file), "Combine complete.")
        except Exception as exc:  # noqa: BLE001
            self._show_error(exc)

    def run_delete_pages(self) -> None:
        try:
            input_file = _existing_file_from_input(self.delete_input.text(), "Input PDF")
            output_file = _output_file_from_input(self.delete_output.text(), "Output PDF")
            pages = self.delete_pages.value()
            if not pages:
                raise ValueError("Pages to delete are required.")
            self._run_async(
                lambda: delete_page_range(input_file, output_file, pages),
                "Delete pages complete.",
            )
        except Exception as exc:  # noqa: BLE001
            self._show_error(exc)

    def run_reorder(self) -> None:
        try:
            input_file = _existing_file_from_input(self.reorder_input.text(), "Input PDF")
            output_file = _output_file_from_input(self.reorder_output.text(), "Output PDF")
            order = self.reorder_order.text().strip()
            if not order:
                raise ValueError("Page order is required.")
            self._run_async(
                lambda: reorder_pages(input_file, output_file, order),
                "Reorder complete.",
            )
        except Exception as exc:  # noqa: BLE001
            self._show_error(exc)

    def run_split(self) -> None:
        try:
            input_file = _existing_file_from_input(self.split_input.text(), "Input PDF")
            output_dir = _output_dir_from_input(self.split_output_dir.text(), "Output Folder")
            pages = self.split_pages.value()
            prefix = self.split_prefix.text().strip() or None

            def _operation() -> str:
                written_files = split_pdf(input_file, output_dir, pages, prefix)
                return f"Split complete. Created {len(written_files)} files."

            self._run_async(_operation, "Split complete.")
        except Exception as exc:  # noqa: BLE001
            self._show_error(exc)

    def run_images_to_pdf(self) -> None:
        try:
            inputs = self.images_inputs.paths()
            if not inputs:
                raise ValueError("At least one input image is required.")
            for file_path in inputs:
                if not file_path.exists() or not file_path.is_file():
                    raise FileNotFoundError(f"Input image not found: {file_path}")
            output_file = _output_file_from_input(self.images_output.text(), "Output PDF")
            self._run_async(lambda: images_to_pdf(inputs, output_file), "Conversion complete.")
        except Exception as exc:  # noqa: BLE001
            self._show_error(exc)

    def run_pdf_to_text(self) -> None:
        try:
            input_file = _existing_file_from_input(self.text_input.text(), "Input PDF")
            output_file = _output_file_from_input(self.text_output.text(), "Output Text")
            self._run_async(
                lambda: pdf_to_text(input_file, output_file),
                "Text extraction complete.",
            )
        except Exception as exc:  # noqa: BLE001
            self._show_error(exc)

    def run_print(self) -> None:
        try:
            input_file = _existing_file_from_input(self.print_input.text(), "Input PDF")
            self._run_async(lambda: print_pdf(input_file), "Print command submitted.")
        except Exception as exc:  # noqa: BLE001
            self._show_error(exc)

    def run_extract_pages(self) -> None:
        try:
            input_file = _existing_file_from_input(self.extract_input.text(), "Input PDF")
            output_file = _output_file_from_input(self.extract_output.text(), "Output PDF")
            pages = self.extract_pages.value()
            if not pages:
                raise ValueError("Pages to extract are required.")
            self._run_async(
                lambda: extract_pages(input_file, output_file, pages),
                "Extract pages complete.",
            )
        except Exception as exc:  # noqa: BLE001
            self._show_error(exc)

    def run_insert_pages(self) -> None:
        try:
            input_file = _existing_file_from_input(self.insert_input.text(), "Base PDF")
            insert_file = _existing_file_from_input(self.insert_file.text(), "Insert PDF")
            output_file = _output_file_from_input(self.insert_output.text(), "Output PDF")
            position = self.insert_at.value()
            pages = self.insert_pages_field.value()
            self._run_async(
                lambda: insert_pages(input_file, insert_file, output_file, position, pages),
                "Insert pages complete.",
            )
        except Exception as exc:  # noqa: BLE001
            self._show_error(exc)

    def run_show_metadata(self) -> None:
        try:
            input_file = _existing_file_from_input(self.meta_input.text(), "Input PDF")
            entries = get_metadata(input_file)
            self.meta_title.setText(entries.get("Title", ""))
            self.meta_author.setText(entries.get("Author", ""))
            self.meta_subject.setText(entries.get("Subject", ""))
            self.meta_keywords.setText(entries.get("Keywords", ""))
            self.status.showMessage(f"Loaded metadata from {input_file.name}", 8000)
        except Exception as exc:  # noqa: BLE001
            self._show_error(exc)

    def run_save_metadata(self) -> None:
        try:
            input_file = _existing_file_from_input(self.meta_input.text(), "Input PDF")
            output_file = _output_file_from_input(self.meta_output.text(), "Output PDF")
            metadata = {
                "Title": self.meta_title.text(),
                "Author": self.meta_author.text(),
                "Subject": self.meta_subject.text(),
                "Keywords": self.meta_keywords.text(),
            }
            metadata = {key: value for key, value in metadata.items() if value}
            self._run_async(
                lambda: set_metadata(input_file, output_file, metadata),
                "Metadata saved.",
            )
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
                pages = self.batch_pages_split.text().strip() or None
                prefix = self.batch_prefix.text().strip() or None
            elif action == "excel-to-pdf":
                paper_size = self.batch_excel_paper_size.currentText()

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

            effective_workers = 1 if action in OFFICE_ACTIONS else workers
            self.batch_worker = BatchWorker(
                action,
                input_dir,
                output_dir,
                pattern,
                effective_workers,
                self.batch_angle.value(),
                pages,
                order,
                text,
                opacity,
                self.batch_font_size.value(),
                prefix,
                self.batch_continue_on_error.isChecked(),
                paper_size,
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
    window.show()
    return app.exec()
