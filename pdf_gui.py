from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
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
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from pdf_tool import (
    batch_delete_pages_pdfs,
    batch_excel_to_pdf,
    batch_pdf_to_text,
    batch_print_pdfs,
    batch_reorder_pdfs,
    batch_rotate_pdfs,
    batch_split_pdfs,
    batch_watermark_pdfs,
    batch_word_to_pdf,
    combine_pdfs,
    DEFAULT_EXCEL_PAPER_SIZE,
    DEFAULT_GLOB_PATTERN,
    DEFAULT_WATERMARK_FONT_SIZE,
    DEFAULT_WATERMARK_OPACITY,
    DEFAULT_WORKERS,
    delete_page_range,
    EXCEL_PAPER_SIZES,
    images_to_pdf,
    MAX_WORKERS,
    MIN_WORKERS,
    pdf_to_text,
    print_pdf,
    reorder_pages,
    rotate_pdf,
    split_pdf,
    watermark_pdf,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


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
    path = Path(text).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    return path


def _output_file_from_input(raw_text: str, label: str) -> Path:
    text = raw_text.strip()
    if not text:
        raise ValueError(f"{label} is required.")
    path = Path(text).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _output_dir_from_input(raw_text: str, label: str) -> Path:
    text = raw_text.strip()
    if not text:
        raise ValueError(f"{label} is required.")
    path = Path(text).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _existing_dir_from_input(raw_text: str, label: str) -> Path:
    text = raw_text.strip()
    if not text:
        raise ValueError(f"{label} is required.")
    path = Path(text).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise FileNotFoundError(f"{label} not found: {path}")
    return path


class BatchWorker(QObject):
    progress = Signal(int, int, str)
    log = Signal(str)
    finished = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        action: str,
        input_dir: Path,
        output_dir: Path | None,
        pattern: str,
        workers: int,
        angle: int,
        pages: str | None,
        order: str | None,
        text: str | None,
        opacity: float,
        font_size: int,
        prefix: str | None,
        continue_on_error: bool,
        paper_size: str | None = None,
    ) -> None:
        super().__init__()
        self.action = action
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.pattern = pattern
        self.workers = workers
        self.angle = angle
        self.pages = pages
        self.order = order
        self.text = text
        self.opacity = opacity
        self.font_size = font_size
        self.prefix = prefix
        self.continue_on_error = continue_on_error
        self.paper_size = paper_size

    def _matching_count(self) -> int:
        files = [path for path in self.input_dir.glob(self.pattern) if path.is_file()]
        if self.action in {"word-to-pdf", "excel-to-pdf"}:
            files = [
                path
                for path in files
                if not path.name.startswith("~$") and path.suffix.lower() not in {".tmp", ".wbk"}
            ]
        return len(files)

    @Slot()
    def run(self) -> None:
        try:
            total = self._matching_count()
            self.progress.emit(0, total, f"Starting {self.action}...")

            if self.action == "rotate":
                if self.output_dir is None:
                    raise ValueError("Output folder is required.")
                written_files = batch_rotate_pdfs(
                    self.input_dir,
                    self.output_dir,
                    self.angle,
                    self.pages,
                    self.pattern,
                    self.workers,
                )
                self.progress.emit(len(written_files), total, "Batch rotate complete")
                self.finished.emit(f"Batch rotate complete. Created {len(written_files)} files.")

            elif self.action == "watermark":
                if self.output_dir is None:
                    raise ValueError("Output folder is required.")
                if not self.text:
                    raise ValueError("Watermark text is required.")
                if self.opacity < 0.0 or self.opacity > 1.0:
                    raise ValueError("Opacity must be between 0.0 and 1.0.")

                written_files = batch_watermark_pdfs(
                    self.input_dir,
                    self.output_dir,
                    self.text,
                    self.opacity,
                    self.font_size,
                    self.pattern,
                    self.workers,
                )
                self.progress.emit(len(written_files), total, "Batch watermark complete")
                self.finished.emit(f"Batch watermark complete. Created {len(written_files)} files.")

            elif self.action == "delete-pages":
                if self.output_dir is None:
                    raise ValueError("Output folder is required.")
                if not self.pages:
                    raise ValueError("Pages are required for batch delete-pages.")
                written_files = batch_delete_pages_pdfs(
                    self.input_dir,
                    self.output_dir,
                    self.pages,
                    self.pattern,
                    self.workers,
                )
                self.progress.emit(len(written_files), total, "Batch delete-pages complete")
                self.finished.emit(f"Batch delete-pages complete. Created {len(written_files)} files.")

            elif self.action == "reorder":
                if self.output_dir is None:
                    raise ValueError("Output folder is required.")
                if not self.order:
                    raise ValueError("Order is required for batch reorder.")
                written_files = batch_reorder_pdfs(
                    self.input_dir,
                    self.output_dir,
                    self.order,
                    self.pattern,
                    self.workers,
                )
                self.progress.emit(len(written_files), total, "Batch reorder complete")
                self.finished.emit(f"Batch reorder complete. Created {len(written_files)} files.")

            elif self.action == "split":
                if self.output_dir is None:
                    raise ValueError("Output folder is required.")
                created_count = batch_split_pdfs(
                    self.input_dir,
                    self.output_dir,
                    self.pages,
                    self.prefix,
                    self.pattern,
                    self.workers,
                )
                self.progress.emit(total, total, "Batch split complete")
                self.finished.emit(f"Batch split complete. Created {created_count} files.")

            elif self.action == "pdf-to-text":
                if self.output_dir is None:
                    raise ValueError("Output folder is required.")
                written_files = batch_pdf_to_text(
                    self.input_dir,
                    self.output_dir,
                    self.pattern,
                    self.workers,
                )
                self.progress.emit(len(written_files), total, "Batch PDF to text complete")
                self.finished.emit(f"Batch PDF to text complete. Created {len(written_files)} files.")

            elif self.action == "word-to-pdf":
                if self.output_dir is None:
                    raise ValueError("Output folder is required.")

                errors: list[str] = []

                def on_progress(index: int, count: int, input_file: Path, state: str) -> None:
                    self.progress.emit(index, count, f"Word {index}/{count}: {input_file.name}")
                    if state == "start":
                        self.log.emit(f"[START] {input_file.name}")
                    elif state == "ok":
                        self.log.emit(f"[OK] {input_file.name}")
                    else:
                        self.log.emit(f"[ERROR] {input_file.name} - {state}")

                written_files = batch_word_to_pdf(
                    self.input_dir,
                    self.output_dir,
                    self.pattern,
                    1,
                    on_progress,
                    self.continue_on_error,
                    errors,
                )

                if errors:
                    self.log.emit("----- Errors -----")
                    for message in errors:
                        self.log.emit(message)

                self.finished.emit(
                    f"Batch Word to PDF complete. Created {len(written_files)} files. Errors: {len(errors)}"
                )

            elif self.action == "excel-to-pdf":
                if self.output_dir is None:
                    raise ValueError("Output folder is required.")

                errors: list[str] = []
                paper_size = self.paper_size or DEFAULT_EXCEL_PAPER_SIZE

                def on_progress(index: int, count: int, input_file: Path, state: str) -> None:
                    self.progress.emit(index, count, f"Excel {index}/{count}: {input_file.name}")
                    if state == "start":
                        self.log.emit(f"[START] {input_file.name}")
                    elif state == "ok":
                        self.log.emit(f"[OK] {input_file.name}")
                    else:
                        self.log.emit(f"[ERROR] {input_file.name} - {state}")

                written_files = batch_excel_to_pdf(
                    self.input_dir,
                    self.output_dir,
                    self.pattern,
                    1,
                    on_progress,
                    self.continue_on_error,
                    errors,
                    paper_size,
                )

                if errors:
                    self.log.emit("----- Errors -----")
                    for message in errors:
                        self.log.emit(message)

                self.finished.emit(
                    f"Batch Excel to PDF complete. Created {len(written_files)} files. Errors: {len(errors)}"
                )

            elif self.action == "print":
                printed_count = batch_print_pdfs(self.input_dir, self.pattern, self.workers)
                self.progress.emit(total, total, "Batch print complete")
                self.finished.emit(f"Batch print submitted for {printed_count} files.")

            else:
                raise ValueError(f"Unsupported batch action: {self.action}")

        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PDF Editor Tool")
        self.resize(960, 680)

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
        tabs.addTab(self._build_edit_tab(), "Edit")
        tabs.addTab(self._build_split_tab(), "Split")
        tabs.addTab(self._build_combine_tab(), "Combine")
        tabs.addTab(self._build_convert_tab(), "Convert")
        tabs.addTab(self._build_batch_tab(), "Batch")
        tabs.addTab(self._build_print_tab(), "Print")
        root_layout.addWidget(tabs, stretch=1)

        self.setCentralWidget(central)
        self.batch_thread: QThread | None = None
        self.batch_worker: BatchWorker | None = None

    def _build_edit_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        rotate_box = QGroupBox("Rotate")
        rotate_form = QFormLayout(rotate_box)

        rotate_in_row, self.rotate_input, _ = _file_row("Input PDF", "Browse", filter_text="PDF Files (*.pdf)")
        rotate_out_row, self.rotate_output, _ = _save_row("Output PDF", "Save As", filter_text="PDF Files (*.pdf)")

        self.rotate_angle = QSpinBox()
        self.rotate_angle.setMinimum(-360)
        self.rotate_angle.setMaximum(360)
        self.rotate_angle.setSingleStep(90)
        self.rotate_angle.setValue(90)

        self.rotate_pages = QLineEdit()
        self.rotate_pages.setPlaceholderText("Optional page range, e.g. 1,3,5-7")

        rotate_button = QPushButton("Run Rotate")
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
            "Output PDF", "Save As", filter_text="PDF Files (*.pdf)"
        )

        self.watermark_text = QLineEdit()
        self.watermark_text.setPlaceholderText("CONFIDENTIAL")

        self.watermark_opacity = QLineEdit(str(DEFAULT_WATERMARK_OPACITY))
        self.watermark_size = QSpinBox()
        self.watermark_size.setMinimum(8)
        self.watermark_size.setMaximum(300)
        self.watermark_size.setValue(DEFAULT_WATERMARK_FONT_SIZE)

        watermark_button = QPushButton("Run Watermark")
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
        delete_out_row, self.delete_output, _ = _save_row("Output PDF", "Save As", filter_text="PDF Files (*.pdf)")
        self.delete_pages = QLineEdit()
        self.delete_pages.setPlaceholderText("Required page range, e.g. 2,4,6-8")

        delete_button = QPushButton("Run Delete Pages")
        delete_button.clicked.connect(self.run_delete_pages)

        delete_form.addRow(delete_in_row)
        delete_form.addRow(delete_out_row)
        delete_form.addRow("Pages", self.delete_pages)
        delete_form.addRow(delete_button)

        reorder_box = QGroupBox("Reorder Pages")
        reorder_form = QFormLayout(reorder_box)

        reorder_in_row, self.reorder_input, _ = _file_row("Input PDF", "Browse", filter_text="PDF Files (*.pdf)")
        reorder_out_row, self.reorder_output, _ = _save_row("Output PDF", "Save As", filter_text="PDF Files (*.pdf)")
        self.reorder_order = QLineEdit()
        self.reorder_order.setPlaceholderText("Required full order, e.g. 3,1,2")

        reorder_button = QPushButton("Run Reorder")
        reorder_button.clicked.connect(self.run_reorder)

        reorder_form.addRow(reorder_in_row)
        reorder_form.addRow(reorder_out_row)
        reorder_form.addRow("Order", self.reorder_order)
        reorder_form.addRow(reorder_button)

        layout.addWidget(rotate_box)
        layout.addWidget(watermark_box)
        layout.addWidget(delete_box)
        layout.addWidget(reorder_box)
        layout.addStretch(1)
        return tab

    def _build_split_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        split_in_row, self.split_input, _ = _file_row("Input PDF", "Browse", filter_text="PDF Files (*.pdf)")
        split_out_row, self.split_output_dir, _ = _directory_row("Output Folder", "Select Folder")
        self.split_pages = QLineEdit()
        self.split_pages.setPlaceholderText("Optional pages, e.g. 1,3,5-7")
        self.split_prefix = QLineEdit()
        self.split_prefix.setPlaceholderText("Optional filename prefix")

        split_button = QPushButton("Run Split")
        split_button.clicked.connect(self.run_split)

        form.addRow(split_in_row)
        form.addRow(split_out_row)
        form.addRow("Pages", self.split_pages)
        form.addRow("Prefix", self.split_prefix)
        form.addRow(split_button)
        return tab

    def _build_combine_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)

        in_row, self.combine_inputs, _ = _file_row(
            "Input PDFs", "Select Files", pick_many=True, filter_text="PDF Files (*.pdf)"
        )
        out_row, self.combine_output, _ = _save_row("Output PDF", "Save As", filter_text="PDF Files (*.pdf)")

        combine_button = QPushButton("Run Combine")
        combine_button.clicked.connect(self.run_combine)

        form.addRow(in_row)
        form.addRow(out_row)
        form.addRow(combine_button)
        return tab

    def _build_convert_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        images_box = QGroupBox("Images to PDF")
        images_form = QFormLayout(images_box)

        img_in_row, self.images_inputs, _ = _file_row(
            "Input Images",
            "Select Images",
            pick_many=True,
            filter_text="Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp)",
        )
        img_out_row, self.images_output, _ = _save_row("Output PDF", "Save As", filter_text="PDF Files (*.pdf)")

        images_button = QPushButton("Run Images to PDF")
        images_button.clicked.connect(self.run_images_to_pdf)

        images_form.addRow(img_in_row)
        images_form.addRow(img_out_row)
        images_form.addRow(images_button)

        text_box = QGroupBox("PDF to Text")
        text_form = QFormLayout(text_box)

        text_in_row, self.text_input, _ = _file_row("Input PDF", "Browse", filter_text="PDF Files (*.pdf)")
        text_out_row, self.text_output, _ = _save_row("Output Text", "Save As", filter_text="Text Files (*.txt)")

        text_button = QPushButton("Run PDF to Text")
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
        print_button = QPushButton("Print on Default Printer")
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
        self.batch_opacity = QLineEdit(str(DEFAULT_WATERMARK_OPACITY))
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

        self.batch_status = QLabel("Idle")
        self.batch_progress = QProgressBar()
        self.batch_progress.setRange(0, 1)
        self.batch_progress.setValue(0)

        self.batch_log = QPlainTextEdit()
        self.batch_log.setReadOnly(True)
        self.batch_log.setPlaceholderText("Batch progress and errors will appear here.")
        self.batch_log.document().setMaximumBlockCount(2000)

        # Connect action change
        self.batch_action.currentIndexChanged.connect(self._on_batch_action_changed)

        # Add to form
        form.addRow("Action", self.batch_action)
        form.addRow(in_row)
        form.addRow(out_row)
        form.addRow("Pattern", self.batch_pattern)
        form.addRow("Workers", self.batch_workers)
        form.addRow(self.batch_continue_on_error)
        form.addRow("Options", self.batch_action_stacked)
        form.addRow(self.run_batch_button)
        form.addRow("Status", self.batch_status)
        form.addRow("Progress", self.batch_progress)

        layout.addWidget(batch_box)
        layout.addWidget(self.batch_log)
        layout.addStretch(1)

        # Initialize field visibility
        self._on_batch_action_changed()
        return tab

    def _on_batch_action_changed(self) -> None:
        """Update displayed fields based on selected batch action."""
        action = self.batch_action.currentData()
        
        # Map actions to stacked widget indices (0=empty, 1=rotate, 2=watermark, etc.)
        action_to_index = {
            "rotate": 1,
            "watermark": 2,
            "delete-pages": 3,
            "reorder": 4,
            "split": 5,
            "pdf-to-text": 6,
            "word-to-pdf": 7,
            "excel-to-pdf": 8,
            "print": 9,
        }
        
        index = action_to_index.get(action, 0)
        self.batch_action_stacked.setCurrentIndex(index)
        
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
        needs_output = action in {
            "rotate",
            "watermark",
            "delete-pages",
            "reorder",
            "split",
            "pdf-to-text",
            "word-to-pdf",
            "excel-to-pdf",
        }
        self.batch_output_dir.setEnabled(needs_output)

        # Handle Office-specific settings
        office_action = action in {"word-to-pdf", "excel-to-pdf"}
        if office_action and self.batch_workers.value() != 1:
            self.batch_workers.setValue(1)
        self.batch_workers.setEnabled(not office_action)
        self.batch_continue_on_error.setEnabled(office_action)

    def _show_success(self, message: str) -> None:
        QMessageBox.information(self, "Success", message)

    def _show_error(self, error: Exception) -> None:
        QMessageBox.critical(self, "Error", str(error))

    def run_rotate(self) -> None:
        try:
            input_file = _existing_file_from_input(self.rotate_input.text(), "Input PDF")
            output_file = _output_file_from_input(self.rotate_output.text(), "Output PDF")
            pages = self.rotate_pages.text().strip() or None
            logger.info(f"Starting rotation: {input_file} -> {output_file}")
            rotate_pdf(input_file, output_file, self.rotate_angle.value(), pages)
            logger.info("Rotation completed successfully")
            self._show_success("Rotation complete.")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Rotation failed: {exc}", exc_info=True)
            self._show_error(exc)

    def run_watermark(self) -> None:
        try:
            input_file = _existing_file_from_input(self.watermark_input.text(), "Input PDF")
            output_file = _output_file_from_input(self.watermark_output.text(), "Output PDF")
            text = self.watermark_text.text().strip()
            if not text:
                raise ValueError("Watermark text is required.")
            opacity = float(self.watermark_opacity.text())
            if opacity < 0.0 or opacity > 1.0:
                raise ValueError("Opacity must be between 0.0 and 1.0.")
            logger.info(f"Starting watermark: {input_file} -> {output_file}, text='{text}'")
            watermark_pdf(
                input_file,
                output_file,
                text,
                opacity,
                self.watermark_size.value(),
            )
            logger.info("Watermark completed successfully")
            self._show_success("Watermark complete.")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Watermark failed: {exc}", exc_info=True)
            self._show_error(exc)

    def run_combine(self) -> None:
        try:
            inputs = _paths_from_input(self.combine_inputs.text())
            if not inputs:
                raise ValueError("At least one input PDF is required.")
            for file_path in inputs:
                if not file_path.exists() or not file_path.is_file():
                    raise FileNotFoundError(f"Input PDF not found: {file_path}")
            output_file = _output_file_from_input(self.combine_output.text(), "Output PDF")
            combine_pdfs(inputs, output_file)
            self._show_success("Combine complete.")
        except Exception as exc:  # noqa: BLE001
            self._show_error(exc)

    def run_delete_pages(self) -> None:
        try:
            input_file = _existing_file_from_input(self.delete_input.text(), "Input PDF")
            output_file = _output_file_from_input(self.delete_output.text(), "Output PDF")
            pages = self.delete_pages.text().strip()
            if not pages:
                raise ValueError("Pages to delete are required.")
            delete_page_range(input_file, output_file, pages)
            self._show_success("Delete pages complete.")
        except Exception as exc:  # noqa: BLE001
            self._show_error(exc)

    def run_reorder(self) -> None:
        try:
            input_file = _existing_file_from_input(self.reorder_input.text(), "Input PDF")
            output_file = _output_file_from_input(self.reorder_output.text(), "Output PDF")
            order = self.reorder_order.text().strip()
            if not order:
                raise ValueError("Page order is required.")
            reorder_pages(input_file, output_file, order)
            self._show_success("Reorder complete.")
        except Exception as exc:  # noqa: BLE001
            self._show_error(exc)

    def run_split(self) -> None:
        try:
            input_file = _existing_file_from_input(self.split_input.text(), "Input PDF")
            output_dir = _output_dir_from_input(self.split_output_dir.text(), "Output Folder")
            pages = self.split_pages.text().strip() or None
            prefix = self.split_prefix.text().strip() or None
            written_files = split_pdf(input_file, output_dir, pages, prefix)
            self._show_success(f"Split complete. Created {len(written_files)} files.")
        except Exception as exc:  # noqa: BLE001
            self._show_error(exc)

    def run_images_to_pdf(self) -> None:
        try:
            inputs = _paths_from_input(self.images_inputs.text())
            if not inputs:
                raise ValueError("At least one input image is required.")
            for file_path in inputs:
                if not file_path.exists() or not file_path.is_file():
                    raise FileNotFoundError(f"Input image not found: {file_path}")
            output_file = _output_file_from_input(self.images_output.text(), "Output PDF")
            images_to_pdf(inputs, output_file)
            self._show_success("Conversion complete.")
        except Exception as exc:  # noqa: BLE001
            self._show_error(exc)

    def run_pdf_to_text(self) -> None:
        try:
            input_file = _existing_file_from_input(self.text_input.text(), "Input PDF")
            output_file = _output_file_from_input(self.text_output.text(), "Output Text")
            pdf_to_text(input_file, output_file)
            self._show_success("Text extraction complete.")
        except Exception as exc:  # noqa: BLE001
            self._show_error(exc)

    def run_print(self) -> None:
        try:
            input_file = _existing_file_from_input(self.print_input.text(), "Input PDF")
            print_pdf(input_file)
            self._show_success("Print command submitted.")
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
            if action in {
                "rotate",
                "watermark",
                "delete-pages",
                "reorder",
                "split",
                "pdf-to-text",
                "word-to-pdf",
                "excel-to-pdf",
            }:
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
                opacity = float(self.batch_opacity.text())
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
            self.run_batch_button.setEnabled(False)

            effective_workers = 1 if action in {"word-to-pdf", "excel-to-pdf"} else workers
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

    def _append_batch_log(self, message: str) -> None:
        self.batch_log.appendPlainText(message)

    def _on_batch_progress(self, current: int, total: int, status: str) -> None:
        if total <= 0:
            self.batch_progress.setRange(0, 0)
        else:
            self.batch_progress.setRange(0, total)
            self.batch_progress.setValue(min(current, total))
        self.batch_status.setText(status)

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
        self.run_batch_button.setEnabled(True)
        if self.batch_worker is not None:
            self.batch_worker.deleteLater()
        if self.batch_thread is not None:
            self.batch_thread.deleteLater()
        self.batch_worker = None
        self.batch_thread = None


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())