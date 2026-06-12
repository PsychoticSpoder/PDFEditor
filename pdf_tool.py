from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import importlib
import io
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, TypeVar

from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

T = TypeVar("T")
OfficeProgressCallback = Callable[[int, int, Path, str], None]

# Configuration constants
OFFICE_BATCH_RESTART_EVERY = 20
DEFAULT_WATERMARK_OPACITY = 0.25
DEFAULT_WATERMARK_FONT_SIZE = 48
DEFAULT_GLOB_PATTERN = "*.pdf"
DEFAULT_WORD_PATTERN = "*.doc*"
DEFAULT_EXCEL_PATTERN = "*.xls*"
MAX_WORKERS = 64
MIN_WORKERS = 1
DEFAULT_WORKERS = 4
WATERMARK_ROTATION_ANGLE = 45

# Excel PDF export constants
EXCEL_PAPER_SIZES = {
    "Letter": 1,  # 8.5 x 11 inches
    "Legal": 5,   # 8.5 x 14 inches
    "A4": 9,      # 210 x 297 mm
    "A3": 8,      # 297 x 420 mm
    "Tabloid": 2, # 11 x 17 inches
    "Ledger": 3,  # 17 x 11 inches
}
DEFAULT_EXCEL_PAPER_SIZE = "A4"


def _validate_existing_file(path_text: str) -> Path:
    path = Path(path_text).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    return path


def _validate_output_path(path_text: str) -> Path:
    path = Path(path_text).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _validate_output_dir(path_text: str) -> Path:
    path = Path(path_text).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _validate_existing_dir(path_text: str) -> Path:
    path = Path(path_text).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise FileNotFoundError(f"Directory not found: {path}")
    return path


def _collect_input_files(input_dir: Path, pattern: str) -> list[Path]:
    """Collect files from a directory matching the given pattern.
    
    Args:
        input_dir: Directory to search for files.
        pattern: Glob pattern for file matching (e.g., "*.pdf").
    
    Returns:
        Sorted list of matching file paths.
    
    Raises:
        ValueError: If no files match the pattern.
    """
    file_pattern = pattern.strip() if pattern.strip() else "*.pdf"
    files = sorted(path for path in input_dir.glob(file_pattern) if path.is_file())
    if not files:
        raise ValueError(f"No files matched pattern '{file_pattern}' in: {input_dir}")
    return files


def _is_office_temp_file(path: Path) -> bool:
    name = path.name
    return name.startswith("~$") or path.suffix.lower() in {".tmp", ".wbk", ".asd"}


def _filter_office_batch_files(files: list[Path]) -> list[Path]:
    # Ignore Office lock/temp files that are not real source documents.
    filtered = [path for path in files if not _is_office_temp_file(path)]
    if not filtered:
        raise ValueError("No valid Office source files found after filtering temp/lock files.")
    return filtered


def _ensure_pdf_output(output_file: Path) -> None:
    if not output_file.exists() or not output_file.is_file():
        raise RuntimeError(f"Expected output file was not created: {output_file}")

    with output_file.open("rb") as stream:
        header = stream.read(5)

    if not header.startswith(b"%PDF"):
        raise RuntimeError(
            f"Office export did not produce a valid PDF for: {output_file}. "
            "Use single-worker Office conversion (workers=1)."
        )


def _resolve_workers(workers: int | None) -> int:
    if workers is None or workers == 0:
        return max(1, min(8, os.cpu_count() or 1))
    if workers < 0:
        raise ValueError("Workers must be 0 or a positive integer.")
    return workers


def _parallel_process(items: list[Path], processor: Callable[[Path], T], workers: int | None) -> list[T]:
    """Process items in parallel using a thread pool.
    
    Args:
        items: List of items to process.
        processor: Function to apply to each item.
        workers: Number of worker threads (None for auto-detection).
    
    Returns:
        List of processed results in the same order as input items.
    
    Raises:
        RuntimeError: If parallel processing fails to return all results.
    """
    if not items:
        return []

    max_workers = _resolve_workers(workers)
    if max_workers == 1 or len(items) == 1:
        return [processor(item) for item in items]

    results: list[T | None] = [None] * len(items)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {executor.submit(processor, item): index for index, item in enumerate(items)}
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            results[index] = future.result()

    if any(result is None for result in results):
        raise RuntimeError("Parallel processing did not return all expected results.")

    return [result for result in results if result is not None]


def _add_workers_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--workers",
        type=int,
        default=0,
        help="Number of worker threads. Use 0 for automatic selection.",
    )


def _emit_office_progress(
    callback: OfficeProgressCallback | None,
    index: int,
    total: int,
    input_file: Path,
    state: str,
) -> None:
    if callback is not None:
        callback(index, total, input_file, state)


def _parse_page_range(page_spec: str | None, total_pages: int) -> set[int]:
    if not page_spec:
        return set(range(total_pages))

    selected: set[int] = set()
    for part in page_spec.split(","):
        token = part.strip()
        if not token:
            continue

        if "-" in token:
            start_text, end_text = token.split("-", maxsplit=1)
            start = int(start_text)
            end = int(end_text)
            if start > end:
                raise ValueError(f"Invalid page range: {token}")
            for page_number in range(start, end + 1):
                if page_number < 1 or page_number > total_pages:
                    raise ValueError(f"Page out of bounds: {page_number}")
                selected.add(page_number - 1)
        else:
            page_number = int(token)
            if page_number < 1 or page_number > total_pages:
                raise ValueError(f"Page out of bounds: {page_number}")
            selected.add(page_number - 1)

    if not selected:
        raise ValueError("No pages selected.")
    return selected


def _parse_page_order(order_spec: str, total_pages: int) -> list[int]:
    tokens = [part.strip() for part in order_spec.split(",") if part.strip()]
    if not tokens:
        raise ValueError("Page order is required.")

    order: list[int] = []
    for token in tokens:
        page_number = int(token)
        if page_number < 1 or page_number > total_pages:
            raise ValueError(f"Page out of bounds: {page_number}")
        order.append(page_number - 1)

    if len(order) != total_pages:
        raise ValueError(
            f"Page order must contain exactly {total_pages} page numbers (1-based), once each."
        )

    if len(set(order)) != total_pages:
        raise ValueError("Page order must not contain duplicate pages.")

    return order


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

    with output_file.open("wb") as stream:
        writer.write(stream)
    
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

    with output_file.open("wb") as stream:
        writer.write(stream)


def reorder_pages(input_file: Path, output_file: Path, order_spec: str) -> None:
    reader = PdfReader(str(input_file))
    writer = PdfWriter()

    page_order = _parse_page_order(order_spec, len(reader.pages))
    for page_index in page_order:
        writer.add_page(reader.pages[page_index])

    with output_file.open("wb") as stream:
        writer.write(stream)


def split_pdf(input_file: Path, output_dir: Path, pages: str | None, prefix: str | None) -> list[Path]:
    """Split a PDF into individual page files.
    
    Args:
        input_file: Path to the input PDF file.
        output_dir: Directory to write split PDF files.
        pages: Optional page range to split (e.g., "1,3,5-7").
        prefix: Optional prefix for output filenames.
    
    Returns:
        List of paths to created PDF files.
    """
    logger.info(f"Splitting {input_file} in {output_dir}")
    reader = PdfReader(str(input_file))

    if pages:
        selected_pages = sorted(_parse_page_range(pages, len(reader.pages)))
    else:
        selected_pages = list(range(len(reader.pages)))

    name_prefix = prefix.strip() if prefix and prefix.strip() else input_file.stem
    written_files: list[Path] = []

    for page_index in selected_pages:
        writer = PdfWriter()
        writer.add_page(reader.pages[page_index])
        output_file = output_dir / f"{name_prefix}_page_{page_index + 1:03d}.pdf"
        with output_file.open("wb") as stream:
            writer.write(stream)
        written_files.append(output_file)

    logger.info(f"Split complete: created {len(written_files)} files")
    return written_files


def watermark_pdf(input_file: Path, output_file: Path, text: str, opacity: float = DEFAULT_WATERMARK_OPACITY, font_size: int = DEFAULT_WATERMARK_FONT_SIZE) -> None:
    """Add a text watermark to a PDF file.
    
    Args:
        input_file: Path to the input PDF file.
        output_file: Path to the output PDF file.
        text: Watermark text to display.
        opacity: Watermark opacity (0.0 to 1.0, default: 0.25).
        font_size: Font size for watermark text (default: 48).
    
    Raises:
        ValueError: If opacity is outside valid range.
    """
    logger.info(f"Adding watermark to {input_file}: text='{text}', opacity={opacity}")
    reader = PdfReader(str(input_file))
    writer = PdfWriter()

    for page in reader.pages:
        page_width = float(page.mediabox.width)
        page_height = float(page.mediabox.height)

        # Build a watermark page matching each page size so overlay stays aligned.
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
        watermark_page = PdfReader(packet).pages[0]
        page.merge_page(watermark_page)
        writer.add_page(page)

    with output_file.open("wb") as stream:
        writer.write(stream)
    
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

    with output_file.open("wb") as stream:
        writer.write(stream)
    
    logger.info("Combine operation completed successfully")


def images_to_pdf(input_images: list[Path], output_file: Path) -> None:
    if not input_images:
        raise ValueError("At least one image is required.")

    loaded_images: list[Image.Image] = []
    try:
        for image_path in input_images:
            image = Image.open(image_path).convert("RGB")
            loaded_images.append(image)

        first_image, *rest_images = loaded_images
        first_image.save(output_file, save_all=True, append_images=rest_images)
    finally:
        for image in loaded_images:
            image.close()


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

    output_file.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"Text extraction complete: {output_file}")


def print_pdf(input_file: Path) -> None:
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
) -> list[Path]:
    """Rotate pages in multiple PDF files in a directory.
    
    Args:
        input_dir: Directory containing input PDF files.
        output_dir: Directory to write rotated PDF files.
        angle: Rotation angle in degrees (must be multiple of 90).
        pages: Optional page range to rotate (e.g., "1,3,5-7").
        pattern: Glob pattern for file matching.
        workers: Number of worker threads.
    
    Returns:
        List of paths to created PDF files.
    """
    logger.info(f"Batch rotating PDFs in {input_dir} by {angle} degrees")
    files = _collect_input_files(input_dir, pattern)

    def _process(input_file: Path) -> Path:
        output_file = output_dir / f"{input_file.stem}_rotated.pdf"
        rotate_pdf(input_file, output_file, angle, pages)
        return output_file

    result = _parallel_process(files, _process, workers)
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
        watermark_pdf(input_file, output_file, text, opacity, font_size)
        return output_file

    result = _parallel_process(files, _process, workers)
    logger.info(f"Batch watermark complete: {len(result)} files")
    return result


def batch_delete_pages_pdfs(
    input_dir: Path,
    output_dir: Path,
    pages: str,
    pattern: str,
    workers: int | None = None,
) -> list[Path]:
    files = _collect_input_files(input_dir, pattern)

    def _process(input_file: Path) -> Path:
        output_file = output_dir / f"{input_file.stem}_deleted.pdf"
        delete_page_range(input_file, output_file, pages)
        return output_file

    return _parallel_process(files, _process, workers)


def batch_reorder_pdfs(
    input_dir: Path,
    output_dir: Path,
    order_spec: str,
    pattern: str,
    workers: int | None = None,
) -> list[Path]:
    files = _collect_input_files(input_dir, pattern)

    def _process(input_file: Path) -> Path:
        output_file = output_dir / f"{input_file.stem}_reordered.pdf"
        reorder_pages(input_file, output_file, order_spec)
        return output_file

    return _parallel_process(files, _process, workers)


def batch_split_pdfs(
    input_dir: Path,
    output_dir: Path,
    pages: str | None,
    prefix: str | None,
    pattern: str,
    workers: int | None = None,
) -> int:
    files = _collect_input_files(input_dir, pattern)

    def _process(input_file: Path) -> int:
        nested_output_dir = output_dir / input_file.stem
        nested_output_dir.mkdir(parents=True, exist_ok=True)
        name_prefix = prefix if prefix else input_file.stem
        written_files = split_pdf(input_file, nested_output_dir, pages, name_prefix)
        return len(written_files)

    counts = _parallel_process(files, _process, workers)
    return sum(counts)


def batch_pdf_to_text(
    input_dir: Path,
    output_dir: Path,
    pattern: str,
    workers: int | None = None,
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
        pdf_to_text(input_file, output_file)
        return output_file

    result = _parallel_process(files, _process, workers)
    logger.info(f"Batch PDF to text complete: {len(result)} files")
    return result


def batch_print_pdfs(input_dir: Path, pattern: str, workers: int | None = None) -> int:
    files = _collect_input_files(input_dir, pattern)

    def _process(input_file: Path) -> int:
        print_pdf(input_file)
        return 1

    results = _parallel_process(files, _process, workers)
    return sum(results)


def _get_windows_com_client():
    if not sys.platform.startswith("win"):
        raise RuntimeError("Word/Excel to PDF conversion is only supported on Windows.")

    try:
        win32 = importlib.import_module("win32com.client")
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency 'pywin32'. Install requirements and ensure Microsoft Office is installed."
        ) from exc

    return win32


def _initialize_com_if_available() -> object | None:
    try:
        pythoncom = importlib.import_module("pythoncom")
    except ImportError:
        return None

    pythoncom.CoInitialize()
    return pythoncom


def word_to_pdf(input_file: Path, output_file: Path) -> None:
    win32 = _get_windows_com_client()
    pythoncom = _initialize_com_if_available()
    word_app = None
    document = None
    
    try:
        word_app = win32.DispatchEx("Word.Application")
        word_app.Visible = False
        document = word_app.Documents.Open(str(input_file))
        if hasattr(document, "SaveAs2"):
            document.SaveAs2(str(output_file), FileFormat=17)
        else:
            document.SaveAs(str(output_file), FileFormat=17)
        _ensure_pdf_output(output_file)
    finally:
        try:
            if document is not None:
                document.Close(False)
        except Exception:
            pass
        try:
            if word_app is not None:
                word_app.Quit()
        except Exception:
            pass
        if pythoncom is not None:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass


def excel_to_pdf(input_file: Path, output_file: Path, paper_size: str = DEFAULT_EXCEL_PAPER_SIZE) -> None:
    """Convert an Excel file to PDF with all columns fitted on one page.
    
    Args:
        input_file: Path to the input Excel file.
        output_file: Path to the output PDF file.
        paper_size: Paper size for PDF export (Letter, Legal, A4, A3, Tabloid, Ledger).
    
    Raises:
        RuntimeError: If conversion fails or output file is not created.
    """
    win32 = _get_windows_com_client()
    pythoncom = _initialize_com_if_available()
    excel_app = None
    workbook = None
    
    try:
        excel_app = win32.DispatchEx("Excel.Application")
        excel_app.Visible = False
        excel_app.DisplayAlerts = False
        workbook = excel_app.Workbooks.Open(str(input_file))
        
        # Configure page setup for all worksheets
        # Mimic what Excel does when you manually export to PDF
        for worksheet in workbook.Worksheets:
            page_setup = worksheet.PageSetup
            
            try:
                # Clear any existing print area to let Excel auto-detect
                page_setup.PrintArea = ""
                
                # This is the KEY setting that Excel uses:
                # Fit all columns on ONE page wide, allow natural height
                page_setup.FitToPagesWide = 1
                page_setup.FitToPagesTall = False  # Don't force height
                
                # Set Zoom to False so FitToPages takes control
                page_setup.Zoom = False
                
                # Set orientation based on column count
                used_range = worksheet.UsedRange
                column_count = used_range.Columns.Count
                
                if column_count > 10:
                    page_setup.Orientation = 1  # Landscape
                else:
                    page_setup.Orientation = 2  # Portrait
                
            except Exception:
                # Fallback to safe defaults
                page_setup.FitToPagesWide = 1
                page_setup.FitToPagesTall = False
                page_setup.Zoom = False
            
            # Use standard paper size (Excel's default)
            paper_size_code = EXCEL_PAPER_SIZES.get(paper_size, EXCEL_PAPER_SIZES["A4"])
            page_setup.PaperSize = paper_size_code
            
            # Standard margins
            page_setup.CenterHorizontally = True
            page_setup.CenterVertically = False
            page_setup.LeftMargin = 0.5 * 72
            page_setup.RightMargin = 0.5 * 72
            page_setup.TopMargin = 0.75 * 72
            page_setup.BottomMargin = 0.75 * 72
            page_setup.HeaderMargin = 0.3 * 72
            page_setup.FooterMargin = 0.3 * 72
        
        # Clear page breaks between worksheets to prevent blank pages
        for i, worksheet in enumerate(workbook.Worksheets):
            if i > 0:  # Skip first worksheet
                try:
                    worksheet.ResetAllPageBreaks()
                except Exception:
                    pass
        
        # Export to PDF
        workbook.ExportAsFixedFormat(0, str(output_file))
        _ensure_pdf_output(output_file)
    finally:
        try:
            if workbook is not None:
                workbook.Close(False)
        except Exception:
            pass
        try:
            if excel_app is not None:
                excel_app.Quit()
        except Exception:
            pass
        if pythoncom is not None:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass


def batch_word_to_pdf(
    input_dir: Path,
    output_dir: Path,
    pattern: str,
    workers: int | None = None,
    progress_callback: OfficeProgressCallback | None = None,
    continue_on_error: bool = False,
    errors: list[str] | None = None,
) -> list[Path]:
    files = _filter_office_batch_files(_collect_input_files(input_dir, pattern))
    win32 = _get_windows_com_client()
    error_list = errors if errors is not None else []
    total = len(files)

    written_files: list[Path] = []
    for start in range(0, total, OFFICE_BATCH_RESTART_EVERY):
        chunk = files[start : start + OFFICE_BATCH_RESTART_EVERY]
        pythoncom = _initialize_com_if_available()
        word_app = None
        try:
            word_app = win32.DispatchEx("Word.Application")
            word_app.Visible = False
            word_app.DisplayAlerts = 0
            for offset, input_file in enumerate(chunk, start=1):
                index = start + offset
                output_file = output_dir / f"{input_file.stem}.pdf"
                _emit_office_progress(progress_callback, index, total, input_file, "start")

                document = None
                try:
                    document = word_app.Documents.Open(
                        str(input_file),
                        ConfirmConversions=False,
                        ReadOnly=True,
                        AddToRecentFiles=False,
                        Revert=False,
                        NoEncodingDialog=True,
                    )
                    if hasattr(document, "SaveAs2"):
                        document.SaveAs2(str(output_file), FileFormat=17)
                    else:
                        document.SaveAs(str(output_file), FileFormat=17)
                    _ensure_pdf_output(output_file)
                    written_files.append(output_file)
                    _emit_office_progress(progress_callback, index, total, input_file, "ok")
                except Exception as exc:
                    message = f"{input_file.name}: {exc}"
                    error_list.append(message)
                    _emit_office_progress(progress_callback, index, total, input_file, f"error: {exc}")
                    if not continue_on_error:
                        raise RuntimeError(message) from exc
                finally:
                    try:
                        if document is not None:
                            document.Close(False)
                    except Exception:
                        pass
        finally:
            try:
                if word_app is not None:
                    word_app.Quit()
            except Exception:
                pass
            if pythoncom is not None:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

    return written_files


def batch_excel_to_pdf(
    input_dir: Path,
    output_dir: Path,
    pattern: str,
    workers: int | None = None,
    progress_callback: OfficeProgressCallback | None = None,
    continue_on_error: bool = False,
    errors: list[str] | None = None,
    paper_size: str = DEFAULT_EXCEL_PAPER_SIZE,
) -> list[Path]:
    """Convert multiple Excel files to PDF in batch.
    
    Args:
        input_dir: Directory containing input Excel files.
        output_dir: Directory to write PDF files.
        pattern: Glob pattern for file matching.
        workers: Number of worker threads (forced to 1 for COM stability).
        progress_callback: Optional callback for progress updates.
        continue_on_error: Whether to continue on errors.
        errors: List to collect error messages.
        paper_size: Paper size for PDF export.
    
    Returns:
        List of paths to created PDF files.
    """
    files = _filter_office_batch_files(_collect_input_files(input_dir, pattern))
    win32 = _get_windows_com_client()
    error_list = errors if errors is not None else []
    total = len(files)

    written_files: list[Path] = []
    for start in range(0, total, OFFICE_BATCH_RESTART_EVERY):
        chunk = files[start : start + OFFICE_BATCH_RESTART_EVERY]
        pythoncom = _initialize_com_if_available()
        excel_app = None
        try:
            excel_app = win32.DispatchEx("Excel.Application")
            excel_app.Visible = False
            excel_app.DisplayAlerts = False
            for offset, input_file in enumerate(chunk, start=1):
                index = start + offset
                output_file = output_dir / f"{input_file.stem}.pdf"
                _emit_office_progress(progress_callback, index, total, input_file, "start")

                workbook = None
                temp_pdfs = []
                try:
                    workbook = excel_app.Workbooks.Open(str(input_file), ReadOnly=True)
                    
                    # Export each worksheet separately to avoid page break issues
                    for worksheet in workbook.Worksheets:
                        try:
                            # Create temp PDF for this worksheet
                            temp_pdf = output_dir / f"_{worksheet.Name}.pdf"
                            temp_pdfs.append(temp_pdf)
                            
                            # Configure page setup for this worksheet only
                            page_setup = worksheet.PageSetup
                            
                            # Clear print area and use FitToPages
                            page_setup.PrintArea = ""
                            page_setup.FitToPagesWide = 1
                            page_setup.FitToPagesTall = False
                            page_setup.Zoom = False
                            
                            # Set orientation based on column count
                            try:
                                used_range = worksheet.UsedRange
                                column_count = used_range.Columns.Count
                                if column_count > 10:
                                    page_setup.Orientation = 1  # Landscape
                                else:
                                    page_setup.Orientation = 2  # Portrait
                            except Exception:
                                page_setup.Orientation = 2  # Portrait
                            
                            # Set paper size
                            paper_size_code = EXCEL_PAPER_SIZES.get(paper_size, EXCEL_PAPER_SIZES["A4"])
                            page_setup.PaperSize = paper_size_code
                            
                            # Export this worksheet to PDF
                            worksheet.ExportAsFixedFormat(0, str(temp_pdf))
                            
                        except Exception as ws_exc:
                            logger.warning(f"Worksheet {worksheet.Name} export failed: {ws_exc}")
                            # Continue with other worksheets
                    
                    # Combine all worksheet PDFs into one file
                    if temp_pdfs:
                        if len(temp_pdfs) == 1:
                            # Only one worksheet, just rename
                            temp_pdfs[0].rename(output_file)
                        else:
                            # Multiple worksheets, combine them
                            _combine_pdfs_in_order(temp_pdfs, output_file)
                            # Clean up temp files
                            for temp_pdf in temp_pdfs:
                                try:
                                    temp_pdf.unlink()
                                except Exception:
                                    pass
                        
                        _ensure_pdf_output(output_file)
                        written_files.append(output_file)
                        _emit_office_progress(progress_callback, index, total, input_file, "ok")
                    else:
                        raise RuntimeError("No worksheets were exported")
                        
                except Exception as exc:
                    message = f"{input_file.name}: {exc}"
                    error_list.append(message)
                    _emit_office_progress(progress_callback, index, total, input_file, f"error: {exc}")
                    if not continue_on_error:
                        raise RuntimeError(message) from exc
                finally:
                    # Clean up any remaining temp files
                    for temp_pdf in temp_pdfs:
                        try:
                            if temp_pdf.exists():
                                temp_pdf.unlink()
                        except Exception:
                            pass
                    
                    try:
                        if workbook is not None:
                            workbook.Close(False)
                    except Exception:
                        pass
        finally:
            try:
                if excel_app is not None:
                    excel_app.Quit()
            except Exception:
                pass
            if pythoncom is not None:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

    return written_files


def _combine_pdfs_in_order(input_pdfs: list[Path], output_file: Path) -> None:
    """Combine multiple PDF files into one in the specified order."""
    from pypdf import PdfWriter, PdfReader
    
    writer = PdfWriter()
    
    for pdf_path in input_pdfs:
        try:
            reader = PdfReader(str(pdf_path))
            for page in reader.pages:
                writer.add_page(page)
        except Exception as e:
            logger.warning(f"Failed to add {pdf_path} to combined PDF: {e}")
    
    with output_file.open("wb") as stream:
        writer.write(stream)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf-tool",
        description="Edit, split, combine, convert, print, and batch-process PDF files.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    edit_parser = subparsers.add_parser("edit", help="Edit PDF files")
    edit_subparsers = edit_parser.add_subparsers(dest="edit_command", required=True)

    rotate_parser = edit_subparsers.add_parser("rotate", help="Rotate pages in a PDF")
    rotate_parser.add_argument("-i", "--input", required=True, help="Input PDF path")
    rotate_parser.add_argument("-o", "--output", required=True, help="Output PDF path")
    rotate_parser.add_argument(
        "-a",
        "--angle",
        required=True,
        type=int,
        help="Rotation angle (multiple of 90, clockwise)",
    )
    rotate_parser.add_argument(
        "-p",
        "--pages",
        default=None,
        help="Pages to rotate, e.g. 1,3,5-7. Defaults to all pages.",
    )

    watermark_parser = edit_subparsers.add_parser("watermark", help="Add text watermark")
    watermark_parser.add_argument("-i", "--input", required=True, help="Input PDF path")
    watermark_parser.add_argument("-o", "--output", required=True, help="Output PDF path")
    watermark_parser.add_argument("-t", "--text", required=True, help="Watermark text")
    watermark_parser.add_argument(
        "--opacity",
        type=float,
        default=DEFAULT_WATERMARK_OPACITY,
        help=f"Watermark opacity from 0.0 to 1.0 (default: {DEFAULT_WATERMARK_OPACITY})",
    )
    watermark_parser.add_argument(
        "--font-size",
        type=int,
        default=DEFAULT_WATERMARK_FONT_SIZE,
        help=f"Watermark font size (default: {DEFAULT_WATERMARK_FONT_SIZE})",
    )

    delete_pages_parser = edit_subparsers.add_parser(
        "delete-pages", help="Delete one or more pages from a PDF"
    )
    delete_pages_parser.add_argument("-i", "--input", required=True, help="Input PDF path")
    delete_pages_parser.add_argument("-o", "--output", required=True, help="Output PDF path")
    delete_pages_parser.add_argument(
        "-p",
        "--pages",
        required=True,
        help="Pages to delete, e.g. 2,4,6-8",
    )

    reorder_parser = edit_subparsers.add_parser("reorder", help="Reorder all pages in a PDF")
    reorder_parser.add_argument("-i", "--input", required=True, help="Input PDF path")
    reorder_parser.add_argument("-o", "--output", required=True, help="Output PDF path")
    reorder_parser.add_argument(
        "--order",
        required=True,
        help="Complete 1-based page order, e.g. 3,1,2",
    )

    split_parser = subparsers.add_parser("split", help="Split a PDF into one file per page")
    split_parser.add_argument("-i", "--input", required=True, help="Input PDF path")
    split_parser.add_argument(
        "-d",
        "--output-dir",
        required=True,
        help="Output directory where split files are written",
    )
    split_parser.add_argument(
        "-p",
        "--pages",
        default=None,
        help="Optional pages to split, e.g. 1,3,5-7. Defaults to all pages.",
    )
    split_parser.add_argument(
        "--prefix",
        default=None,
        help="Optional output file prefix. Defaults to input filename stem.",
    )

    combine_parser = subparsers.add_parser("combine", help="Combine multiple PDFs")
    combine_parser.add_argument(
        "-i",
        "--inputs",
        required=True,
        nargs="+",
        help="Input PDF files in order",
    )
    combine_parser.add_argument("-o", "--output", required=True, help="Output PDF path")

    convert_parser = subparsers.add_parser("convert", help="Convert files")
    convert_subparsers = convert_parser.add_subparsers(dest="convert_command", required=True)

    images_to_pdf_parser = convert_subparsers.add_parser(
        "images-to-pdf", help="Convert one or more images into a PDF"
    )
    images_to_pdf_parser.add_argument(
        "-i", "--inputs", required=True, nargs="+", help="Input image files"
    )
    images_to_pdf_parser.add_argument("-o", "--output", required=True, help="Output PDF path")

    pdf_to_text_parser = convert_subparsers.add_parser("pdf-to-text", help="Extract text from a PDF")
    pdf_to_text_parser.add_argument("-i", "--input", required=True, help="Input PDF path")
    pdf_to_text_parser.add_argument("-o", "--output", required=True, help="Output text file path")

    print_parser = subparsers.add_parser("print", help="Print a PDF on the default printer")
    print_parser.add_argument("-i", "--input", required=True, help="Input PDF path")

    batch_parser = subparsers.add_parser("batch", help="Batch process folders")
    batch_subparsers = batch_parser.add_subparsers(dest="batch_command", required=True)

    batch_rotate_parser = batch_subparsers.add_parser("rotate", help="Rotate all matching PDFs in a folder")
    batch_rotate_parser.add_argument("--input-dir", required=True, help="Input folder path")
    batch_rotate_parser.add_argument("--output-dir", required=True, help="Output folder path")
    batch_rotate_parser.add_argument("--angle", required=True, type=int, help="Rotation angle")
    batch_rotate_parser.add_argument(
        "--pages",
        default=None,
        help="Optional pages to rotate for each file, e.g. 1,3,5-7",
    )
    batch_rotate_parser.add_argument(
        "--pattern",
        default=DEFAULT_GLOB_PATTERN,
        help=f"Glob pattern (default: {DEFAULT_GLOB_PATTERN})",
    )
    _add_workers_argument(batch_rotate_parser)

    batch_watermark_parser = batch_subparsers.add_parser(
        "watermark", help="Add watermark to all matching PDFs in a folder"
    )
    batch_watermark_parser.add_argument("--input-dir", required=True, help="Input folder path")
    batch_watermark_parser.add_argument("--output-dir", required=True, help="Output folder path")
    batch_watermark_parser.add_argument("--text", required=True, help="Watermark text")
    batch_watermark_parser.add_argument(
        "--opacity",
        type=float,
        default=DEFAULT_WATERMARK_OPACITY,
        help=f"Opacity 0.0-1.0 (default: {DEFAULT_WATERMARK_OPACITY})",
    )
    batch_watermark_parser.add_argument(
        "--font-size",
        type=int,
        default=DEFAULT_WATERMARK_FONT_SIZE,
        help=f"Font size (default: {DEFAULT_WATERMARK_FONT_SIZE})",
    )
    batch_watermark_parser.add_argument(
        "--pattern",
        default=DEFAULT_GLOB_PATTERN,
        help=f"Glob pattern (default: {DEFAULT_GLOB_PATTERN})",
    )
    _add_workers_argument(batch_watermark_parser)

    batch_delete_parser = batch_subparsers.add_parser(
        "delete-pages", help="Delete pages from all matching PDFs in a folder"
    )
    batch_delete_parser.add_argument("--input-dir", required=True, help="Input folder path")
    batch_delete_parser.add_argument("--output-dir", required=True, help="Output folder path")
    batch_delete_parser.add_argument("--pages", required=True, help="Pages to delete, e.g. 2,4,6-8")
    batch_delete_parser.add_argument(
        "--pattern",
        default=DEFAULT_GLOB_PATTERN,
        help=f"Glob pattern (default: {DEFAULT_GLOB_PATTERN})",
    )
    _add_workers_argument(batch_delete_parser)

    batch_reorder_parser = batch_subparsers.add_parser(
        "reorder", help="Reorder pages in all matching PDFs in a folder"
    )
    batch_reorder_parser.add_argument("--input-dir", required=True, help="Input folder path")
    batch_reorder_parser.add_argument("--output-dir", required=True, help="Output folder path")
    batch_reorder_parser.add_argument("--order", required=True, help="Complete page order, e.g. 3,1,2")
    batch_reorder_parser.add_argument(
        "--pattern",
        default=DEFAULT_GLOB_PATTERN,
        help=f"Glob pattern (default: {DEFAULT_GLOB_PATTERN})",
    )
    _add_workers_argument(batch_reorder_parser)

    batch_split_parser = batch_subparsers.add_parser("split", help="Split all matching PDFs in a folder")
    batch_split_parser.add_argument("--input-dir", required=True, help="Input folder path")
    batch_split_parser.add_argument("--output-dir", required=True, help="Output folder path")
    batch_split_parser.add_argument(
        "--pages",
        default=None,
        help="Optional pages to split for each file, e.g. 1,3,5-7",
    )
    batch_split_parser.add_argument(
        "--prefix",
        default=None,
        help="Optional output file prefix for every split group",
    )
    batch_split_parser.add_argument(
        "--pattern",
        default=DEFAULT_GLOB_PATTERN,
        help=f"Glob pattern (default: {DEFAULT_GLOB_PATTERN})",
    )
    _add_workers_argument(batch_split_parser)

    batch_text_parser = batch_subparsers.add_parser(
        "pdf-to-text", help="Extract text for all matching PDFs in a folder"
    )
    batch_text_parser.add_argument("--input-dir", required=True, help="Input folder path")
    batch_text_parser.add_argument("--output-dir", required=True, help="Output folder path")
    batch_text_parser.add_argument(
        "--pattern",
        default=DEFAULT_GLOB_PATTERN,
        help=f"Glob pattern (default: {DEFAULT_GLOB_PATTERN})",
    )
    _add_workers_argument(batch_text_parser)

    batch_print_parser = batch_subparsers.add_parser("print", help="Print all matching PDFs in a folder")
    batch_print_parser.add_argument("--input-dir", required=True, help="Input folder path")
    batch_print_parser.add_argument(
        "--pattern",
        default=DEFAULT_GLOB_PATTERN,
        help=f"Glob pattern (default: {DEFAULT_GLOB_PATTERN})",
    )
    _add_workers_argument(batch_print_parser)

    batch_word_parser = batch_subparsers.add_parser(
        "word-to-pdf", help="Convert matching Microsoft Word files in a folder to PDF"
    )
    batch_word_parser.add_argument("--input-dir", required=True, help="Input folder path")
    batch_word_parser.add_argument("--output-dir", required=True, help="Output folder path")
    batch_word_parser.add_argument(
        "--pattern",
        default=DEFAULT_WORD_PATTERN,
        help=f"Glob pattern for Word files (default: {DEFAULT_WORD_PATTERN})",
    )
    _add_workers_argument(batch_word_parser)

    batch_excel_parser = batch_subparsers.add_parser(
        "excel-to-pdf", help="Convert matching Microsoft Excel files in a folder to PDF"
    )
    batch_excel_parser.add_argument("--input-dir", required=True, help="Input folder path")
    batch_excel_parser.add_argument("--output-dir", required=True, help="Output folder path")
    batch_excel_parser.add_argument(
        "--pattern",
        default=DEFAULT_EXCEL_PATTERN,
        help=f"Glob pattern for Excel files (default: {DEFAULT_EXCEL_PATTERN})",
    )
    batch_excel_parser.add_argument(
        "--paper-size",
        type=str,
        default=DEFAULT_EXCEL_PAPER_SIZE,
        choices=list(EXCEL_PAPER_SIZES.keys()),
        help=f"Paper size for PDF export (default: {DEFAULT_EXCEL_PAPER_SIZE})",
    )
    _add_workers_argument(batch_excel_parser)

    dnd_parser = subparsers.add_parser("dnd", help="Drag-and-drop-friendly positional commands")
    dnd_subparsers = dnd_parser.add_subparsers(dest="dnd_command", required=True)

    dnd_combine_parser = dnd_subparsers.add_parser("combine", help="Combine dropped PDFs")
    dnd_combine_parser.add_argument("output", help="Output PDF path")
    dnd_combine_parser.add_argument("inputs", nargs="+", help="Input PDF paths")

    dnd_images_parser = dnd_subparsers.add_parser("images-to-pdf", help="Convert dropped images to one PDF")
    dnd_images_parser.add_argument("output", help="Output PDF path")
    dnd_images_parser.add_argument("inputs", nargs="+", help="Input image paths")

    dnd_split_parser = dnd_subparsers.add_parser("split", help="Split a dropped PDF")
    dnd_split_parser.add_argument("input", help="Input PDF path")
    dnd_split_parser.add_argument("output_dir", help="Output folder path")
    dnd_split_parser.add_argument("--pages", default=None, help="Optional pages, e.g. 1,3,5-7")
    dnd_split_parser.add_argument("--prefix", default=None, help="Optional output prefix")

    dnd_text_parser = dnd_subparsers.add_parser("pdf-to-text", help="Convert dropped PDF to text")
    dnd_text_parser.add_argument("input", help="Input PDF path")
    dnd_text_parser.add_argument("output", help="Output text path")

    dnd_print_parser = dnd_subparsers.add_parser("print", help="Print a dropped PDF")
    dnd_print_parser.add_argument("input", help="Input PDF path")

    dnd_word_parser = dnd_subparsers.add_parser("word-to-pdf", help="Convert a dropped Word file to PDF")
    dnd_word_parser.add_argument("input", help="Input Word file path")
    dnd_word_parser.add_argument("output", help="Output PDF path")

    dnd_excel_parser = dnd_subparsers.add_parser("excel-to-pdf", help="Convert a dropped Excel file to PDF")
    dnd_excel_parser.add_argument("input", help="Input Excel file path")
    dnd_excel_parser.add_argument("output", help="Output PDF path")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        logger.info(f"Starting command: {args.command}")
        if args.command == "edit":
            input_file = _validate_existing_file(args.input)
            output_file = _validate_output_path(args.output)

            if args.edit_command == "rotate":
                rotate_pdf(input_file, output_file, args.angle, args.pages)
            elif args.edit_command == "watermark":
                if not (0.0 <= args.opacity <= 1.0):
                    raise ValueError("Opacity must be between 0.0 and 1.0.")
                if args.font_size <= 0:
                    raise ValueError("Font size must be positive.")
                watermark_pdf(input_file, output_file, args.text, args.opacity, args.font_size)
            elif args.edit_command == "delete-pages":
                delete_page_range(input_file, output_file, args.pages)
            elif args.edit_command == "reorder":
                reorder_pages(input_file, output_file, args.order)

        elif args.command == "split":
            input_file = _validate_existing_file(args.input)
            output_dir = _validate_output_dir(args.output_dir)
            written_files = split_pdf(input_file, output_dir, args.pages, args.prefix)
            print(f"Created {len(written_files)} files in: {output_dir}")

        elif args.command == "combine":
            input_files = [_validate_existing_file(path_text) for path_text in args.inputs]
            output_file = _validate_output_path(args.output)
            combine_pdfs(input_files, output_file)

        elif args.command == "convert":
            if args.convert_command == "images-to-pdf":
                input_images = [_validate_existing_file(path_text) for path_text in args.inputs]
                output_file = _validate_output_path(args.output)
                images_to_pdf(input_images, output_file)
            elif args.convert_command == "pdf-to-text":
                input_file = _validate_existing_file(args.input)
                output_file = _validate_output_path(args.output)
                pdf_to_text(input_file, output_file)

        elif args.command == "print":
            input_file = _validate_existing_file(args.input)
            print_pdf(input_file)

        elif args.command == "batch":
            if args.batch_command == "rotate":
                input_dir = _validate_existing_dir(args.input_dir)
                output_dir = _validate_output_dir(args.output_dir)
                written_files = batch_rotate_pdfs(
                    input_dir,
                    output_dir,
                    args.angle,
                    args.pages,
                    args.pattern,
                    args.workers,
                )
                print(f"Created {len(written_files)} files in: {output_dir}")

            elif args.batch_command == "watermark":
                if not (0.0 <= args.opacity <= 1.0):
                    raise ValueError("Opacity must be between 0.0 and 1.0.")
                if args.font_size <= 0:
                    raise ValueError("Font size must be positive.")
                input_dir = _validate_existing_dir(args.input_dir)
                output_dir = _validate_output_dir(args.output_dir)
                written_files = batch_watermark_pdfs(
                    input_dir,
                    output_dir,
                    args.text,
                    args.opacity,
                    args.font_size,
                    args.pattern,
                    args.workers,
                )
                print(f"Created {len(written_files)} files in: {output_dir}")

            elif args.batch_command == "delete-pages":
                input_dir = _validate_existing_dir(args.input_dir)
                output_dir = _validate_output_dir(args.output_dir)
                written_files = batch_delete_pages_pdfs(
                    input_dir,
                    output_dir,
                    args.pages,
                    args.pattern,
                    args.workers,
                )
                print(f"Created {len(written_files)} files in: {output_dir}")

            elif args.batch_command == "reorder":
                input_dir = _validate_existing_dir(args.input_dir)
                output_dir = _validate_output_dir(args.output_dir)
                written_files = batch_reorder_pdfs(
                    input_dir,
                    output_dir,
                    args.order,
                    args.pattern,
                    args.workers,
                )
                print(f"Created {len(written_files)} files in: {output_dir}")

            elif args.batch_command == "split":
                input_dir = _validate_existing_dir(args.input_dir)
                output_dir = _validate_output_dir(args.output_dir)
                created_count = batch_split_pdfs(
                    input_dir,
                    output_dir,
                    args.pages,
                    args.prefix,
                    args.pattern,
                    args.workers,
                )
                print(f"Created {created_count} files in: {output_dir}")

            elif args.batch_command == "pdf-to-text":
                input_dir = _validate_existing_dir(args.input_dir)
                output_dir = _validate_output_dir(args.output_dir)
                written_files = batch_pdf_to_text(input_dir, output_dir, args.pattern, args.workers)
                print(f"Created {len(written_files)} files in: {output_dir}")

            elif args.batch_command == "print":
                input_dir = _validate_existing_dir(args.input_dir)
                printed_count = batch_print_pdfs(input_dir, args.pattern, args.workers)
                print(f"Submitted print for {printed_count} files.")

            elif args.batch_command == "word-to-pdf":
                input_dir = _validate_existing_dir(args.input_dir)
                output_dir = _validate_output_dir(args.output_dir)
                if args.workers > 1:
                    print("Note: Word conversion is forced to single-worker mode for COM stability.")
                written_files = batch_word_to_pdf(input_dir, output_dir, args.pattern, args.workers)
                print(f"Created {len(written_files)} files in: {output_dir}")

            elif args.batch_command == "excel-to-pdf":
                input_dir = _validate_existing_dir(args.input_dir)
                output_dir = _validate_output_dir(args.output_dir)
                if args.workers > 1:
                    print("Note: Excel conversion is forced to single-worker mode for COM stability.")
                written_files = batch_excel_to_pdf(
                    input_dir,
                    output_dir,
                    args.pattern,
                    args.workers,
                    paper_size=args.paper_size,
                )
                print(f"Created {len(written_files)} files in: {output_dir}")

        elif args.command == "dnd":
            if args.dnd_command == "combine":
                input_files = [_validate_existing_file(path_text) for path_text in args.inputs]
                output_file = _validate_output_path(args.output)
                combine_pdfs(input_files, output_file)

            elif args.dnd_command == "images-to-pdf":
                input_images = [_validate_existing_file(path_text) for path_text in args.inputs]
                output_file = _validate_output_path(args.output)
                images_to_pdf(input_images, output_file)

            elif args.dnd_command == "split":
                input_file = _validate_existing_file(args.input)
                output_dir = _validate_output_dir(args.output_dir)
                written_files = split_pdf(input_file, output_dir, args.pages, args.prefix)
                print(f"Created {len(written_files)} files in: {output_dir}")

            elif args.dnd_command == "pdf-to-text":
                input_file = _validate_existing_file(args.input)
                output_file = _validate_output_path(args.output)
                pdf_to_text(input_file, output_file)

            elif args.dnd_command == "print":
                input_file = _validate_existing_file(args.input)
                print_pdf(input_file)

            elif args.dnd_command == "word-to-pdf":
                input_file = _validate_existing_file(args.input)
                output_file = _validate_output_path(args.output)
                word_to_pdf(input_file, output_file)

            elif args.dnd_command == "excel-to-pdf":
                input_file = _validate_existing_file(args.input)
                output_file = _validate_output_path(args.output)
                excel_to_pdf(input_file, output_file, DEFAULT_EXCEL_PAPER_SIZE)

        print("Done.")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())