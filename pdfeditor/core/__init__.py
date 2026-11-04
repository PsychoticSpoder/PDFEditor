"""Core PDF toolkit: constants, helpers, PDF operations and Office conversion.

This package is the single source of truth for validation, action metadata and the
PDF/Office operations used by both the CLI (``pdfeditor.cli``) and the GUI
(``pdfeditor.gui``).
"""

from __future__ import annotations

from .constants import (
    DEFAULT_EXCEL_PAPER_SIZE,
    DEFAULT_EXCEL_PATTERN,
    DEFAULT_GLOB_PATTERN,
    DEFAULT_WATERMARK_FONT_SIZE,
    DEFAULT_WATERMARK_OPACITY,
    DEFAULT_WORKERS,
    DEFAULT_WORD_PATTERN,
    EXCEL_PAPER_SIZES,
    MAX_WORKERS,
    MIN_WORKERS,
    OFFICE_ACTIONS,
    OFFICE_BATCH_RESTART_EVERY,
    OFFICE_OPERATION_TIMEOUT_SECONDS,
    OUTPUT_DIR_ACTIONS,
    WATERMARK_ROTATION_ANGLE,
)
from .io_utils import (
    OfficeProgressCallback,
    OperationCancelled,
    T,
    _add_workers_argument,
    _collect_input_files,
    _emit_office_progress,
    _ensure_pdf_output,
    _filter_office_batch_files,
    _is_office_temp_file,
    _parallel_process,
    _resolve_workers,
    _validate_existing_dir,
    _validate_existing_file,
    _validate_output_dir,
    _validate_output_path,
)
from .office import (
    batch_excel_to_pdf,
    batch_word_to_pdf,
    excel_to_pdf,
    word_to_pdf,
)
from .pdf_ops import (
    _combine_pdfs_in_order,
    _is_blank_page,
    batch_delete_pages_pdfs,
    batch_pdf_to_text,
    batch_print_pdfs,
    batch_reorder_pdfs,
    batch_rotate_pdfs,
    batch_split_pdfs,
    batch_watermark_pdfs,
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
from .ranges import _parse_page_order, _parse_page_range
