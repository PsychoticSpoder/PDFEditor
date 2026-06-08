from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import importlib
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, TypeVar

from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

T = TypeVar("T")
OfficeProgressCallback = Callable[[int, int, Path, str], None]
OFFICE_BATCH_RESTART_EVERY = 20


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
    if angle % 90 != 0:
        raise ValueError("Rotation angle must be a multiple of 90.")

    reader = PdfReader(str(input_file))
    writer = PdfWriter()
    pages_to_rotate = _parse_page_range(pages, len(reader.pages))

    for index, page in enumerate(reader.pages):
        if index in pages_to_rotate:
            page.rotate(angle)
        writer.add_page(page)

    with output_file.open("wb") as stream:
        writer.write(stream)


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

    return written_files


def watermark_pdf(input_file: Path, output_file: Path, text: str, opacity: float, font_size: int) -> None:
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
        watermark_canvas.rotate(45)
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


def combine_pdfs(input_files: list[Path], output_file: Path) -> None:
    writer = PdfWriter()
    for file_path in input_files:
        reader = PdfReader(str(file_path))
        for page in reader.pages:
            writer.add_page(page)

    with output_file.open("wb") as stream:
        writer.write(stream)


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
    reader = PdfReader(str(input_file))
    lines: list[str] = []
    for page_index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        lines.append(f"===== Page {page_index} =====")
        lines.append(text.strip())
        lines.append("")

    output_file.write_text("\n".join(lines), encoding="utf-8")


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
    files = _collect_input_files(input_dir, pattern)

    def _process(input_file: Path) -> Path:
        output_file = output_dir / f"{input_file.stem}_rotated.pdf"
        rotate_pdf(input_file, output_file, angle, pages)
        return output_file

    return _parallel_process(files, _process, workers)


def batch_watermark_pdfs(
    input_dir: Path,
    output_dir: Path,
    text: str,
    opacity: float,
    font_size: int,
    pattern: str,
    workers: int | None = None,
) -> list[Path]:
    files = _collect_input_files(input_dir, pattern)

    def _process(input_file: Path) -> Path:
        output_file = output_dir / f"{input_file.stem}_watermarked.pdf"
        watermark_pdf(input_file, output_file, text, opacity, font_size)
        return output_file

    return _parallel_process(files, _process, workers)


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
    files = _collect_input_files(input_dir, pattern)

    def _process(input_file: Path) -> Path:
        output_file = output_dir / f"{input_file.stem}.txt"
        pdf_to_text(input_file, output_file)
        return output_file

    return _parallel_process(files, _process, workers)


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
    word_app = win32.DispatchEx("Word.Application")
    word_app.Visible = False

    document = None
    try:
        document = word_app.Documents.Open(str(input_file))
        if hasattr(document, "SaveAs2"):
            document.SaveAs2(str(output_file), FileFormat=17)
        else:
            document.SaveAs(str(output_file), FileFormat=17)
        _ensure_pdf_output(output_file)
    finally:
        if document is not None:
            document.Close(False)
        word_app.Quit()
        if pythoncom is not None:
            pythoncom.CoUninitialize()


def excel_to_pdf(input_file: Path, output_file: Path) -> None:
    win32 = _get_windows_com_client()
    pythoncom = _initialize_com_if_available()
    excel_app = win32.DispatchEx("Excel.Application")
    excel_app.Visible = False
    excel_app.DisplayAlerts = False

    workbook = None
    try:
        workbook = excel_app.Workbooks.Open(str(input_file))
        workbook.ExportAsFixedFormat(0, str(output_file))
        _ensure_pdf_output(output_file)
    finally:
        if workbook is not None:
            workbook.Close(False)
        excel_app.Quit()
        if pythoncom is not None:
            pythoncom.CoUninitialize()


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
        word_app = win32.DispatchEx("Word.Application")
        word_app.Visible = False
        word_app.DisplayAlerts = 0
        try:
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
                    if document is not None:
                        document.Close(False)
        finally:
            word_app.Quit()
            if pythoncom is not None:
                pythoncom.CoUninitialize()

    return written_files


def batch_excel_to_pdf(
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
        excel_app = win32.DispatchEx("Excel.Application")
        excel_app.Visible = False
        excel_app.DisplayAlerts = False
        try:
            for offset, input_file in enumerate(chunk, start=1):
                index = start + offset
                output_file = output_dir / f"{input_file.stem}.pdf"
                _emit_office_progress(progress_callback, index, total, input_file, "start")

                workbook = None
                try:
                    workbook = excel_app.Workbooks.Open(str(input_file), ReadOnly=True)
                    workbook.ExportAsFixedFormat(0, str(output_file))
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
                    if workbook is not None:
                        workbook.Close(False)
        finally:
            excel_app.Quit()
            if pythoncom is not None:
                pythoncom.CoUninitialize()

    return written_files


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
        default=0.25,
        help="Watermark opacity from 0.0 to 1.0 (default: 0.25)",
    )
    watermark_parser.add_argument(
        "--font-size",
        type=int,
        default=48,
        help="Watermark font size (default: 48)",
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
    batch_rotate_parser.add_argument("--pattern", default="*.pdf", help="Glob pattern (default: *.pdf)")
    _add_workers_argument(batch_rotate_parser)

    batch_watermark_parser = batch_subparsers.add_parser(
        "watermark", help="Add watermark to all matching PDFs in a folder"
    )
    batch_watermark_parser.add_argument("--input-dir", required=True, help="Input folder path")
    batch_watermark_parser.add_argument("--output-dir", required=True, help="Output folder path")
    batch_watermark_parser.add_argument("--text", required=True, help="Watermark text")
    batch_watermark_parser.add_argument("--opacity", type=float, default=0.25, help="Opacity 0.0-1.0")
    batch_watermark_parser.add_argument("--font-size", type=int, default=48, help="Font size")
    batch_watermark_parser.add_argument("--pattern", default="*.pdf", help="Glob pattern (default: *.pdf)")
    _add_workers_argument(batch_watermark_parser)

    batch_delete_parser = batch_subparsers.add_parser(
        "delete-pages", help="Delete pages from all matching PDFs in a folder"
    )
    batch_delete_parser.add_argument("--input-dir", required=True, help="Input folder path")
    batch_delete_parser.add_argument("--output-dir", required=True, help="Output folder path")
    batch_delete_parser.add_argument("--pages", required=True, help="Pages to delete, e.g. 2,4,6-8")
    batch_delete_parser.add_argument("--pattern", default="*.pdf", help="Glob pattern (default: *.pdf)")
    _add_workers_argument(batch_delete_parser)

    batch_reorder_parser = batch_subparsers.add_parser(
        "reorder", help="Reorder pages in all matching PDFs in a folder"
    )
    batch_reorder_parser.add_argument("--input-dir", required=True, help="Input folder path")
    batch_reorder_parser.add_argument("--output-dir", required=True, help="Output folder path")
    batch_reorder_parser.add_argument("--order", required=True, help="Complete page order, e.g. 3,1,2")
    batch_reorder_parser.add_argument("--pattern", default="*.pdf", help="Glob pattern (default: *.pdf)")
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
    batch_split_parser.add_argument("--pattern", default="*.pdf", help="Glob pattern (default: *.pdf)")
    _add_workers_argument(batch_split_parser)

    batch_text_parser = batch_subparsers.add_parser(
        "pdf-to-text", help="Extract text for all matching PDFs in a folder"
    )
    batch_text_parser.add_argument("--input-dir", required=True, help="Input folder path")
    batch_text_parser.add_argument("--output-dir", required=True, help="Output folder path")
    batch_text_parser.add_argument("--pattern", default="*.pdf", help="Glob pattern (default: *.pdf)")
    _add_workers_argument(batch_text_parser)

    batch_print_parser = batch_subparsers.add_parser("print", help="Print all matching PDFs in a folder")
    batch_print_parser.add_argument("--input-dir", required=True, help="Input folder path")
    batch_print_parser.add_argument("--pattern", default="*.pdf", help="Glob pattern (default: *.pdf)")
    _add_workers_argument(batch_print_parser)

    batch_word_parser = batch_subparsers.add_parser(
        "word-to-pdf", help="Convert matching Microsoft Word files in a folder to PDF"
    )
    batch_word_parser.add_argument("--input-dir", required=True, help="Input folder path")
    batch_word_parser.add_argument("--output-dir", required=True, help="Output folder path")
    batch_word_parser.add_argument(
        "--pattern",
        default="*.doc*",
        help="Glob pattern for Word files (default: *.doc*)",
    )
    _add_workers_argument(batch_word_parser)

    batch_excel_parser = batch_subparsers.add_parser(
        "excel-to-pdf", help="Convert matching Microsoft Excel files in a folder to PDF"
    )
    batch_excel_parser.add_argument("--input-dir", required=True, help="Input folder path")
    batch_excel_parser.add_argument("--output-dir", required=True, help="Output folder path")
    batch_excel_parser.add_argument(
        "--pattern",
        default="*.xls*",
        help="Glob pattern for Excel files (default: *.xls*)",
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
                written_files = batch_excel_to_pdf(input_dir, output_dir, args.pattern, args.workers)
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
                excel_to_pdf(input_file, output_file)

        print("Done.")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())