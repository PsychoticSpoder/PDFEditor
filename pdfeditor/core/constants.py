"""Shared configuration constants for the PDF toolkit."""

from __future__ import annotations

# Batch / Office restart cadence
OFFICE_BATCH_RESTART_EVERY = 20

# Watermark defaults
DEFAULT_WATERMARK_OPACITY = 0.25
DEFAULT_WATERMARK_FONT_SIZE = 48
WATERMARK_ROTATION_ANGLE = 45

# File matching patterns
DEFAULT_GLOB_PATTERN = "*.pdf"
DEFAULT_WORD_PATTERN = "*.doc*"
DEFAULT_EXCEL_PATTERN = "*.xls*"

# Worker pool bounds.
# DEFAULT_WORKERS is used by the GUI spin box; _resolve_workers() auto-selects up to
# 8 threads (or CPU count) when no explicit value is supplied.
MAX_WORKERS = 64
MIN_WORKERS = 1
DEFAULT_WORKERS = 4

# Excel PDF export: paper size name -> Excel XlPaperSize code
EXCEL_PAPER_SIZES = {
    "Letter": 1,  # 8.5 x 11 inches
    "Legal": 5,  # 8.5 x 14 inches
    "A4": 9,  # 210 x 297 mm
    "A3": 8,  # 297 x 420 mm
    "Tabloid": 2,  # 11 x 17 inches
    "Ledger": 3,  # 17 x 11 inches
}
DEFAULT_EXCEL_PAPER_SIZE = "A4"

# Batch actions that require an output directory. Defined once here so the CLI and the
# GUI share a single source of truth.
OUTPUT_DIR_ACTIONS = frozenset(
    {
        "rotate",
        "watermark",
        "delete-pages",
        "reorder",
        "split",
        "pdf-to-text",
        "word-to-pdf",
        "excel-to-pdf",
    }
)

# How an export splits a document's pages: one PDF per input document, one file per page,
# documents of a fixed number of pages, or everything combined into a single PDF. Defined once
# here so the core, the CLI and the GUI (both the Export Pages dialog and the Bulk PDF Editing
# panel) share a single vocabulary.
EXPORT_COMBINED = "combined"
EXPORT_SINGLE = "single"
EXPORT_PER_PAGE = "per-page"
EXPORT_CHUNKED = "chunked"
EXPORT_MODES = frozenset({EXPORT_COMBINED, EXPORT_SINGLE, EXPORT_PER_PAGE, EXPORT_CHUNKED})

# Batch actions that drive Microsoft Office via COM (forced to a single worker).
OFFICE_ACTIONS = frozenset({"word-to-pdf", "excel-to-pdf"})

# Watchdog: force-kill a hung Word/Excel process if a single conversion step takes
# longer than this many seconds. Set to 0 (or negative) to disable.
OFFICE_OPERATION_TIMEOUT_SECONDS = 120.0
