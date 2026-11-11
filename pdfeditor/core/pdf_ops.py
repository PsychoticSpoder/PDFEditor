"""Core PDF operations and batch processors."""

from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

from PIL import Image
from pypdf import PageObject, PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from .constants import (
    DEFAULT_GLOB_PATTERN,
    DEFAULT_WATERMARK_FONT_SIZE,
    DEFAULT_WATERMARK_OPACITY,
    EXPORT_CHUNKED,
    EXPORT_COMBINED,
    EXPORT_MODES,
    EXPORT_PER_PAGE,
    EXPORT_SINGLE,
    WATERMARK_ROTATION_ANGLE,
)
from .io_utils import (
    OperationCancelled,
    _atomic_write_bytes,
    _atomic_write_pdf,
    _atomic_write_text,
    _collect_input_files,
    _parallel_process,
    _unique_path,
)
from .ranges import _parse_page_order, _parse_page_range

logger = logging.getLogger(__name__)



def rotate_pdf(input_file: Path, output_file: Path, angle: int, pages: str | None) -> None:
    """Rotate pages in a PDF file by the specified angle.
    
    Args:
        input_file: Path to the input PDF file.
        output_file: Path to the output PDF file.
        angle: Rotation angle in degrees (must be multiple of 90).
        pages: Optional page range specification (e.g., "1,3,5-7").
    
    Raises:
        ValueError: If angle is not a multiple of 90 or page range is invalid.
    """
    if angle % 90 != 0:
        raise ValueError("Rotation angle must be a multiple of 90.")

    logger.info(f"Rotating {input_file} by {angle} degrees")
    reader = PdfReader(str(input_file))
    writer = PdfWriter()
    pages_to_rotate = _parse_page_range(pages, len(reader.pages))

    for index, page in enumerate(reader.pages):
        if index in pages_to_rotate:
            page.rotate(angle)
        writer.add_page(page)

    _atomic_write_pdf(writer, output_file)
    
    logger.info(f"Rotation complete: {output_file}")


def delete_page_range(input_file: Path, output_file: Path, pages: str) -> None:
    reader = PdfReader(str(input_file))
    writer = PdfWriter()

    pages_to_delete = _parse_page_range(pages, len(reader.pages))
    for index, page in enumerate(reader.pages):
        if index not in pages_to_delete:
            writer.add_page(page)

    if len(writer.pages) == 0:
        raise ValueError("Delete range removed all pages. Output PDF would be empty.")

    _atomic_write_pdf(writer, output_file)


def reorder_pages(input_file: Path, output_file: Path, order_spec: str) -> None:
    reader = PdfReader(str(input_file))
    writer = PdfWriter()

    page_order = _parse_page_order(order_spec, len(reader.pages))
    for page_index in page_order:
        writer.add_page(reader.pages[page_index])

    _atomic_write_pdf(writer, output_file)


def _selected_page_indexes(reader: PdfReader, pages: str | None) -> list[int]:
    """The 0-based indexes of the pages an export covers, in document order.

    ``pages`` uses :func:`_parse_page_range` syntax (e.g. ``"1,3,5-7"``); ``None`` (or an
    empty spec) selects every page.
    """
    if pages:
        return sorted(_parse_page_range(pages, len(reader.pages)))
    return list(range(len(reader.pages)))


def extract_pages(input_file: Path, output_file: Path, pages: str | None = None) -> None:
    """Copy a subset of pages from one PDF into a new PDF.

    Args:
        input_file: Source PDF.
        output_file: Destination PDF containing only the selected pages (in order).
        pages: 1-based page selection, e.g. ``"1,3,5-7"``. ``None`` copies every page, which
            makes this the "keep the whole document as a new file" case of an export.
    """
    reader = PdfReader(str(input_file))
    writer = PdfWriter()
    for page_index in _selected_page_indexes(reader, pages):
        writer.add_page(reader.pages[page_index])
    _atomic_write_pdf(writer, output_file)


def insert_pages(
    input_file: Path,
    insert_file: Path,
    output_file: Path,
    position: int,
    pages: str | None = None,
) -> None:
    """Insert the pages of ``insert_file`` into ``input_file`` at ``position``.

    Args:
        input_file: Base PDF.
        insert_file: PDF whose pages are inserted.
        output_file: Destination PDF.
        position: 1-based index of the base page *before* which to insert. A value
            greater than the base page count appends at the end.
        pages: Optional 1-based selection of pages from ``insert_file`` (default: all).
    """
    base_reader = PdfReader(str(input_file))
    insert_reader = PdfReader(str(insert_file))
    base_count = len(base_reader.pages)

    if position < 1 or position > base_count + 1:
        raise ValueError(f"Insert position must be between 1 and {base_count + 1}.")

    selected = _parse_page_range(pages, len(insert_reader.pages))
    insert_indices = [index for index in range(len(insert_reader.pages)) if index in selected]

    split_at = position - 1
    writer = PdfWriter()
    for page_index in range(split_at):
        writer.add_page(base_reader.pages[page_index])
    for page_index in insert_indices:
        writer.add_page(insert_reader.pages[page_index])
    for page_index in range(split_at, base_count):
        writer.add_page(base_reader.pages[page_index])

    _atomic_write_pdf(writer, output_file)


def get_metadata(input_file: Path) -> dict[str, str]:
    """Return the PDF document metadata (Title, Author, ...) as a plain dict."""
    reader = PdfReader(str(input_file))
    metadata = reader.metadata or {}
    return {str(key).lstrip("/"): str(value) for key, value in metadata.items()}


def set_metadata(input_file: Path, output_file: Path, metadata: dict[str, str]) -> None:
    """Write a copy of ``input_file`` with the given metadata keys set.

    Keys may be given with or without a leading ``/`` (e.g. ``Title`` or ``/Title``).
    Keys that are not supplied are preserved from the source document.
    """
    reader = PdfReader(str(input_file))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    to_set = {f"/{key.lstrip('/')}": value for key, value in metadata.items()}
    writer.add_metadata(to_set)
    _atomic_write_pdf(writer, output_file)


def split_pdf(
    input_file: Path,
    output_dir: Path,
    pages: str | None,
    prefix: str | None,
    chunk_size: int | None = None,
    unique_names: bool = False,
) -> list[Path]:
    """Split a PDF into individual page files, or into groups of pages.

    Args:
        input_file: Path to the input PDF file.
        output_dir: Directory to write split PDF files.
        pages: Optional page range to split (e.g., "1,3,5-7").
        prefix: Optional prefix for output filenames.
        chunk_size: Pages per output file. ``None`` (the default) writes one file per page;
            any other value writes one file per group of that many pages, so a 10-page
            selection with ``chunk_size=2`` becomes five two-page PDFs. The final group may
            be shorter, and the pages keep their original order.
        unique_names: When ``True`` an output that already exists is written beside it with a
            `` (1)``, `` (2)``, ... suffix instead of being overwritten.

    Returns:
        List of paths to created PDF files.
    """
    logger.info(f"Splitting {input_file} in {output_dir}")
    reader = PdfReader(str(input_file))
    selected_pages = _selected_page_indexes(reader, pages)

    name_prefix = prefix.strip() if prefix and prefix.strip() else input_file.stem
    group_size = max(1, chunk_size) if chunk_size else 1
    written_files: list[Path] = []

    for start in range(0, len(selected_pages), group_size):
        group = selected_pages[start : start + group_size]
        writer = PdfWriter()
        for page_index in group:
            writer.add_page(reader.pages[page_index])
        if group_size == 1:
            output_file = output_dir / f"{name_prefix}_page_{group[0] + 1:03d}.pdf"
        else:
            output_file = (
                output_dir / f"{name_prefix}_pages_{group[0] + 1:03d}-{group[-1] + 1:03d}.pdf"
            )
        if unique_names:
            output_file = _unique_path(output_file)
        _atomic_write_pdf(writer, output_file)
        written_files.append(output_file)

    logger.info(f"Split complete: created {len(written_files)} files")
    return written_files


def _watermark_overlay_page(
    page_width: float,
    page_height: float,
    text: str,
    opacity: float,
    font_size: int,
) -> PageObject:
    """Build a one-page watermark overlay sized to match a page, ready to merge."""
    # Matching each page's size keeps the overlay aligned.
    packet = io.BytesIO()
    watermark_canvas = canvas.Canvas(packet, pagesize=(page_width, page_height))
    watermark_canvas.saveState()
    watermark_canvas.translate(page_width / 2, page_height / 2)
    watermark_canvas.rotate(WATERMARK_ROTATION_ANGLE)
    try:
        watermark_canvas.setFillAlpha(opacity)
    except AttributeError:
        pass
    watermark_canvas.setFont("Helvetica-Bold", font_size)
    watermark_canvas.setFillColorRGB(0.55, 0.55, 0.55)
    watermark_canvas.drawCentredString(0, 0, text)
    watermark_canvas.restoreState()
    watermark_canvas.save()

    packet.seek(0)
    return PdfReader(packet).pages[0]


def watermark_pdf(
    input_file: Path,
    output_file: Path,
    text: str,
    opacity: float = DEFAULT_WATERMARK_OPACITY,
    font_size: int = DEFAULT_WATERMARK_FONT_SIZE,
    pages: str | None = None,
) -> None:
    """Add a text watermark to a PDF file.
    
    Args:
        input_file: Path to the input PDF file.
        output_file: Path to the output PDF file.
        text: Watermark text to display.
        opacity: Watermark opacity (0.0 to 1.0, default: 0.25).
        font_size: Font size for watermark text (default: 48).
        pages: Optional 1-based page selection to watermark (default: every page).
    
    Raises:
        ValueError: If opacity is outside valid range.
    """
    logger.info(f"Adding watermark to {input_file}: text='{text}', opacity={opacity}")
    reader = PdfReader(str(input_file))
    writer = PdfWriter()
    pages_to_mark = _parse_page_range(pages, len(reader.pages))

    for index, page in enumerate(reader.pages):
        # Attach the page to the writer before merging so pypdf has a reference.
        writer.add_page(page)
        if index not in pages_to_mark:
            continue

        overlay = _watermark_overlay_page(
            float(page.mediabox.width),
            float(page.mediabox.height),
            text,
            opacity,
            font_size,
        )
        writer.pages[-1].merge_page(overlay)

    _atomic_write_pdf(writer, output_file)
    
    logger.info(f"Watermark complete: {output_file}")


def combine_pdfs(input_files: list[Path], output_file: Path) -> None:
    """Combine multiple PDF files into a single PDF.
    
    Args:
        input_files: List of input PDF files to combine in order.
        output_file: Path to the output combined PDF file.
    """
    logger.info(f"Combining {len(input_files)} PDFs into {output_file}")
    writer = PdfWriter()
    for file_path in input_files:
        reader = PdfReader(str(file_path))
        for page in reader.pages:
            writer.add_page(page)

    _atomic_write_pdf(writer, output_file)
    
    logger.info("Combine operation completed successfully")


def images_to_pdf(input_images: list[Path], output_file: Path) -> None:
    """Convert one or more images into a multi-page PDF.

    Images are streamed one at a time (instead of all loaded into memory at once)
    and each image is placed on its own page sized to the image's aspect ratio.
    The result is written atomically so a mid-way failure cannot leave a corrupt
    PDF behind.
    """
    if not input_images:
        raise ValueError("At least one image is required.")

    buffer = io.BytesIO()
    pdf_canvas = canvas.Canvas(buffer)

    for image_path in input_images:
        with Image.open(image_path) as image:
            width_px, height_px = image.size
            dpi = image.info.get("dpi", (72.0, 72.0))
            dpi_x = float(dpi[0]) if dpi and dpi[0] else 72.0
            dpi_y = float(dpi[1]) if dpi and len(dpi) > 1 and dpi[1] else 72.0
            page_width = max(1.0, width_px * 72.0 / dpi_x)
            page_height = max(1.0, height_px * 72.0 / dpi_y)

            pdf_canvas.setPageSize((page_width, page_height))
            pdf_canvas.drawImage(
                ImageReader(image.convert("RGB")),
                0,
                0,
                width=page_width,
                height=page_height,
                preserveAspectRatio=True,
                anchor="c",
            )
            pdf_canvas.showPage()

    pdf_canvas.save()
    _atomic_write_bytes(output_file, buffer.getvalue())


def pdf_to_text(input_file: Path, output_file: Path) -> None:
    """Extract text content from a PDF file.
    
    Args:
        input_file: Path to the input PDF file.
        output_file: Path to the output text file.
    """
    logger.info(f"Extracting text from {input_file}")
    reader = PdfReader(str(input_file))
    lines: list[str] = []
    for page_index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        lines.append(f"===== Page {page_index} =====")
        lines.append(text.strip())
        lines.append("")

    _atomic_write_text(output_file, "\n".join(lines))
    logger.info(f"Text extraction complete: {output_file}")


def print_pdf(input_file: Path) -> None:
    """Send a PDF to the default printer.

    Note:
        This is best-effort and does **not** confirm the document was printed. On
        Windows it relies on the shell "print" verb (handed to the registered PDF
        handler); elsewhere it uses CUPS ``lp``. Batch callers should treat the
        returned count as "submitted", not "printed".
    """
    if sys.platform.startswith("win"):
        os.startfile(str(input_file), "print")  # type: ignore[attr-defined]
        return

    if shutil.which("lp"):
        subprocess.run(["lp", str(input_file)], check=True)
        return

    raise RuntimeError("Printing is only implemented for Windows and systems with 'lp'.")
def batch_rotate_pdfs(
    input_dir: Path,
    output_dir: Path,
    angle: int,
    pages: str | None,
    pattern: str,
    workers: int | None = None,
    should_cancel: Callable[[], bool] | None = None,
    unique_names: bool = False,
) -> list[Path]:
    """Rotate pages in multiple PDF files in a directory.
    
    Args:
        input_dir: Directory containing input PDF files.
        output_dir: Directory to write rotated PDF files.
        angle: Rotation angle in degrees (must be multiple of 90).
        pages: Optional page range to rotate (e.g., "1,3,5-7").
        pattern: Glob pattern for file matching.
        workers: Number of worker threads.
        should_cancel: Optional cancellation check, polled between files.
        unique_names: When ``True`` an output that already exists is written beside it with a
            `` (1)``, `` (2)``, ... suffix instead of being overwritten.
    
    Returns:
        List of paths to created PDF files.
    """
    logger.info(f"Batch rotating PDFs in {input_dir} by {angle} degrees")
    files = _collect_input_files(input_dir, pattern)

    def _process(input_file: Path) -> Path:
        output_file = output_dir / f"{input_file.stem}_rotated.pdf"
        if unique_names:
            output_file = _unique_path(output_file)
        rotate_pdf(input_file, output_file, angle, pages)
        return output_file

    result = _parallel_process(files, _process, workers, should_cancel)
    logger.info(f"Batch rotate complete: {len(result)} files")
    return result


def batch_watermark_pdfs(
    input_dir: Path,
    output_dir: Path,
    text: str,
    opacity: float = DEFAULT_WATERMARK_OPACITY,
    font_size: int = DEFAULT_WATERMARK_FONT_SIZE,
    pattern: str = DEFAULT_GLOB_PATTERN,
    workers: int | None = None,
    should_cancel: Callable[[], bool] | None = None,
    unique_names: bool = False,
) -> list[Path]:
    """Add watermarks to multiple PDF files in a directory.
    
    Args:
        input_dir: Directory containing input PDF files.
        output_dir: Directory to write watermarked PDF files.
        text: Watermark text to display.
        opacity: Watermark opacity (0.0 to 1.0).
        font_size: Font size for watermark text.
        pattern: Glob pattern for file matching.
        workers: Number of worker threads.
    
    Returns:
        List of paths to created PDF files.
    """
    logger.info(f"Batch watermarking PDFs in {input_dir} with text='{text}'")
    files = _collect_input_files(input_dir, pattern)

    def _process(input_file: Path) -> Path:
        output_file = output_dir / f"{input_file.stem}_watermarked.pdf"
        if unique_names:
            output_file = _unique_path(output_file)
        watermark_pdf(input_file, output_file, text, opacity, font_size)
        return output_file

    result = _parallel_process(files, _process, workers, should_cancel)
    logger.info(f"Batch watermark complete: {len(result)} files")
    return result


def batch_delete_pages_pdfs(
    input_dir: Path,
    output_dir: Path,
    pages: str,
    pattern: str,
    workers: int | None = None,
    should_cancel: Callable[[], bool] | None = None,
    unique_names: bool = False,
) -> list[Path]:
    files = _collect_input_files(input_dir, pattern)

    def _process(input_file: Path) -> Path:
        output_file = output_dir / f"{input_file.stem}_deleted.pdf"
        if unique_names:
            output_file = _unique_path(output_file)
        delete_page_range(input_file, output_file, pages)
        return output_file

    return _parallel_process(files, _process, workers, should_cancel)


def batch_reorder_pdfs(
    input_dir: Path,
    output_dir: Path,
    order_spec: str,
    pattern: str,
    workers: int | None = None,
    should_cancel: Callable[[], bool] | None = None,
    unique_names: bool = False,
) -> list[Path]:
    files = _collect_input_files(input_dir, pattern)

    def _process(input_file: Path) -> Path:
        output_file = output_dir / f"{input_file.stem}_reordered.pdf"
        if unique_names:
            output_file = _unique_path(output_file)
        reorder_pages(input_file, output_file, order_spec)
        return output_file

    return _parallel_process(files, _process, workers, should_cancel)


def _combine_selected_pages(
    input_files: list[Path],
    output_file: Path,
    pages: str | None,
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    """Write the chosen pages of every input PDF into one output PDF, in order."""
    writer = PdfWriter()
    for input_file in input_files:
        if should_cancel is not None and should_cancel():
            raise OperationCancelled("Operation cancelled.")
        reader = PdfReader(str(input_file))
        for page_index in _selected_page_indexes(reader, pages):
            writer.add_page(reader.pages[page_index])
    _atomic_write_pdf(writer, output_file)


def batch_split_pdfs(
    input_dir: Path,
    output_dir: Path,
    pages: str | None,
    prefix: str | None,
    pattern: str,
    workers: int | None = None,
    should_cancel: Callable[[], bool] | None = None,
    mode: str = EXPORT_PER_PAGE,
    chunk_size: int | None = None,
    unique_names: bool = False,
) -> int:
    """Export pages from multiple PDF files in a directory.

    Every output lands directly in ``output_dir`` (no per-document subfolders) and page files
    are named after the PDF they came from, e.g. ``report_page_001.pdf``.

    Args:
        input_dir: Directory containing input PDF files.
        output_dir: Directory to write the exported PDF files.
        pages: Optional page range to export (e.g., "1,3,5-7"); ``None`` exports every page.
        prefix: Optional filename prefix for the exported page files. With several input PDFs
            the source name is kept after the prefix, so files cannot overwrite each other.
        pattern: Glob pattern for file matching.
        workers: Number of worker threads.
        should_cancel: Optional cancellation check, polled between files.
        mode: How to split the pages: :data:`~pdfeditor.core.EXPORT_SINGLE` writes one PDF per
            input file, :data:`~pdfeditor.core.EXPORT_PER_PAGE` (the default) one PDF per page,
            :data:`~pdfeditor.core.EXPORT_CHUNKED` one PDF per group of ``chunk_size`` pages,
            and :data:`~pdfeditor.core.EXPORT_COMBINED` a single PDF holding the chosen pages
            of every matching file.
        chunk_size: Pages per exported file in ``EXPORT_CHUNKED`` mode.
        unique_names: When ``True`` an output that already exists is written beside it with a
            `` (1)``, `` (2)``, ... suffix instead of being overwritten.

    Returns:
        Total number of created PDF files.

    Raises:
        ValueError: If ``mode`` is not one of :data:`~pdfeditor.core.EXPORT_MODES`.
    """
    if mode not in EXPORT_MODES:
        raise ValueError(f"Unknown export mode: {mode!r}")

    logger.info(f"Batch exporting pages from PDFs in {input_dir} ({mode})")
    files = _collect_input_files(input_dir, pattern)
    output_dir.mkdir(parents=True, exist_ok=True)

    if mode == EXPORT_COMBINED:
        if not files:
            return 0
        name = (prefix or "").strip() or "combined"
        combined_output = output_dir / f"{name}_selected.pdf"
        if unique_names:
            combined_output = _unique_path(combined_output)
        _combine_selected_pages(files, combined_output, pages, should_cancel)
        logger.info("Batch export complete: created 1 file")
        return 1

    # A prefix names the page files on its own, but then every input PDF would produce the
    # same names in the shared output folder, so the source name is kept as well.
    tag_sources = bool(prefix) and len(files) > 1

    def _process(input_file: Path) -> int:
        if mode == EXPORT_SINGLE:
            output_file = output_dir / f"{input_file.stem}_selected.pdf"
            if unique_names:
                output_file = _unique_path(output_file)
            extract_pages(input_file, output_file, pages)
            return 1

        name_prefix = f"{prefix}_{input_file.stem}" if tag_sources else prefix or input_file.stem
        written_files = split_pdf(
            input_file,
            output_dir,
            pages,
            name_prefix,
            chunk_size if mode == EXPORT_CHUNKED else None,
            unique_names=unique_names,
        )
        return len(written_files)

    counts = _parallel_process(files, _process, workers, should_cancel)
    logger.info(f"Batch export complete: created {sum(counts)} files")
    return sum(counts)


def batch_pdf_to_text(
    input_dir: Path,
    output_dir: Path,
    pattern: str,
    workers: int | None = None,
    should_cancel: Callable[[], bool] | None = None,
    unique_names: bool = False,
) -> list[Path]:
    """Extract text from multiple PDF files in a directory.
    
    Args:
        input_dir: Directory containing input PDF files.
        output_dir: Directory to write text files.
        pattern: Glob pattern for file matching.
        workers: Number of worker threads.
    
    Returns:
        List of paths to created text files.
    """
    logger.info(f"Batch extracting text from PDFs in {input_dir}")
    files = _collect_input_files(input_dir, pattern)

    def _process(input_file: Path) -> Path:
        output_file = output_dir / f"{input_file.stem}.txt"
        if unique_names:
            output_file = _unique_path(output_file)
        pdf_to_text(input_file, output_file)
        return output_file

    result = _parallel_process(files, _process, workers, should_cancel)
    logger.info(f"Batch PDF to text complete: {len(result)} files")
    return result


def batch_print_pdfs(
    input_dir: Path,
    pattern: str,
    workers: int | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> int:
    files = _collect_input_files(input_dir, pattern)

    def _process(input_file: Path) -> int:
        print_pdf(input_file)
        return 1

    results = _parallel_process(files, _process, workers, should_cancel)
    return sum(results)


def _combine_pdfs_in_order(
    input_pdfs: list[Path],
    output_file: Path,
    skip_blank_pages: bool = False,
) -> None:
    """Combine PDF files into one in order.

    Blank-page removal is opt-in because the text-based heuristic can misclassify
    sparse but meaningful pages (e.g. pages containing only images or charts).
    Every skipped page is logged so false positives are easy to spot.
    """
    
    writer = PdfWriter()
    
    for pdf_path in input_pdfs:
        try:
            reader = PdfReader(str(pdf_path))
            for page in reader.pages:
                if skip_blank_pages and _is_blank_page(page):
                    logger.info("Skipping blank page from %s", pdf_path.name)
                    continue
                writer.add_page(page)
        except Exception as e:
            logger.warning(f"Failed to add {pdf_path} to combined PDF: {e}")
    
    _atomic_write_pdf(writer, output_file)


def _is_blank_page(page) -> bool:
    """
    Check if a PDF page is mostly blank (only contains footer text).
    
    Returns True if the page appears to be blank except for standard footers.
    """
    try:
        # Extract text from the page
        text = page.extract_text()
        
        if not text:
            return True
        
        # Remove whitespace and check character count
        cleaned_text = text.strip()
        
        # If page has very little text, it's likely a footer-only page
        if len(cleaned_text) < 30:
            return True
        
        # Count non-whitespace characters
        content_chars = sum(1 for c in cleaned_text if not c.isspace())
        
        # If less than 50 characters of actual content, likely blank
        if content_chars < 50:
            return True
        
        # Check line count - footer-only pages typically have 1-3 lines
        lines = [line.strip() for line in cleaned_text.split('\n') if line.strip()]
        if len(lines) <= 3:
            # Check if lines look like footers (short, centered text)
            if all(len(line) < 60 for line in lines):
                return True
        
        return False
        
    except Exception:
        # If we can't determine, assume it's not blank
        return False
