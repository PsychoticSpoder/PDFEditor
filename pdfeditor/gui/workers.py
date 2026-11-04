"""Background workers that run batch and single-file operations off the GUI thread."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Signal, Slot

from ..core import (
    DEFAULT_EXCEL_PAPER_SIZE,
    batch_delete_pages_pdfs,
    batch_excel_to_pdf,
    batch_pdf_to_text,
    batch_print_pdfs,
    batch_reorder_pdfs,
    batch_rotate_pdfs,
    batch_split_pdfs,
    batch_watermark_pdfs,
    batch_word_to_pdf,
)



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


class SingleOpWorker(QObject):
    """Run a single (non-batch) operation on a background thread."""

    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, operation: Callable[[], str | None], success_message: str) -> None:
        super().__init__()
        self._operation = operation
        self._success_message = success_message

    @Slot()
    def run(self) -> None:
        try:
            result = self._operation()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            return
        self.finished.emit(result or self._success_message)
