"""Command-line interface for the PDF toolkit."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .core import (
    DEFAULT_EXCEL_PAPER_SIZE,
    DEFAULT_EXCEL_PATTERN,
    DEFAULT_GLOB_PATTERN,
    DEFAULT_WATERMARK_FONT_SIZE,
    DEFAULT_WATERMARK_OPACITY,
    DEFAULT_WORD_PATTERN,
    EXCEL_PAPER_SIZES,
    _add_workers_argument,
    _validate_existing_dir,
    _validate_existing_file,
    _validate_output_dir,
    _validate_output_path as _core_validate_output_path,
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

logger = logging.getLogger(__name__)


def configure_logging(level_name: str) -> None:
    """Configure root logging once, at CLI startup (no import-time side effects)."""
    logging.basicConfig(
        level=getattr(logging, level_name.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


class _SkipExisting(Exception):
    """Internal signal that an operation was skipped because the output exists."""


# Populated from the parsed CLI arguments in main().
_OUTPUT_GUARD: dict[str, bool] = {"overwrite": False, "skip_existing": False}


def _validate_output_path(path_text: str) -> Path:
    """Validate an output path, refusing to clobber existing files unless allowed.

    Wraps :func:`pdfeditor.core._validate_output_path` so ``--overwrite`` /
    ``--skip-existing`` apply uniformly to every single-file output command.
    """
    path = _core_validate_output_path(path_text)
    if path.exists():
        if _OUTPUT_GUARD["skip_existing"]:
            raise _SkipExisting(f"output already exists: {path}")
        if not _OUTPUT_GUARD["overwrite"]:
            raise FileExistsError(
                f"Output already exists: {path} "
                "(use --overwrite to replace it or --skip-existing to ignore it)."
            )
    return path


def _parse_metadata_pairs(items: list[str]) -> dict[str, str]:
    """Parse repeated ``KEY=VALUE`` metadata options into a dict."""
    metadata: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"Metadata entry must be KEY=VALUE: {item!r}")
        key, _, value = item.partition("=")
        key = key.strip()
        if not key:
            raise ValueError(f"Metadata key must not be empty: {item!r}")
        metadata[key.strip()] = value.strip()
    return metadata



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf-tool",
        description="Edit, split, combine, convert, print, and batch-process PDF files.",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging.")
    parser.add_argument("--quiet", "-q", action="store_true", help="Only log errors.")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow overwriting existing output files (default: refuse).",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip writing when the output file already exists.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate arguments and print the plan without writing any files.",
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

    extract_pages_parser = edit_subparsers.add_parser(
        "extract-pages", help="Extract selected pages into a new PDF"
    )
    extract_pages_parser.add_argument("-i", "--input", required=True, help="Input PDF path")
    extract_pages_parser.add_argument("-o", "--output", required=True, help="Output PDF path")
    extract_pages_parser.add_argument(
        "-p", "--pages", required=True, help="Pages to extract, e.g. 1,3,5-7"
    )

    insert_pages_parser = edit_subparsers.add_parser(
        "insert-pages", help="Insert pages from another PDF"
    )
    insert_pages_parser.add_argument("-i", "--input", required=True, help="Base PDF path")
    insert_pages_parser.add_argument("--insert", required=True, help="PDF whose pages are inserted")
    insert_pages_parser.add_argument("-o", "--output", required=True, help="Output PDF path")
    insert_pages_parser.add_argument(
        "--at", type=int, required=True, help="1-based position to insert before"
    )
    insert_pages_parser.add_argument(
        "-p", "--pages", default=None, help="Optional pages from the inserted PDF"
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

    metadata_parser = subparsers.add_parser("metadata", help="Read or write PDF metadata")
    metadata_parser.add_argument("-i", "--input", required=True, help="Input PDF path")
    metadata_parser.add_argument(
        "-o", "--output", default=None, help="Output PDF path (required when using --set)"
    )
    metadata_parser.add_argument(
        "--set",
        dest="metadata",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Metadata entry to set (repeatable), e.g. --set Title=Report",
    )

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

    if getattr(args, "quiet", False):
        configure_logging("ERROR")
    elif getattr(args, "verbose", False):
        configure_logging("DEBUG")
    else:
        configure_logging(os.environ.get("PDF_TOOL_LOG_LEVEL", "INFO"))

    _OUTPUT_GUARD["overwrite"] = getattr(args, "overwrite", False)
    _OUTPUT_GUARD["skip_existing"] = getattr(args, "skip_existing", False)

    if getattr(args, "dry_run", False):
        print(f"[dry-run] {args.command}: no files will be written.")
        return 0

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
            elif args.edit_command == "extract-pages":
                extract_pages(input_file, output_file, args.pages)
            elif args.edit_command == "insert-pages":
                insert_file = _validate_existing_file(args.insert)
                insert_pages(input_file, insert_file, output_file, args.at, args.pages)

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

        elif args.command == "metadata":
            input_file = _validate_existing_file(args.input)
            if args.metadata:
                if not args.output:
                    raise ValueError("--output is required when using --set.")
                output_file = _validate_output_path(args.output)
                set_metadata(input_file, output_file, _parse_metadata_pairs(args.metadata))
                print(f"Wrote metadata to: {output_file}")
            else:
                entries = get_metadata(input_file)
                if entries:
                    for key, value in sorted(entries.items()):
                        print(f"{key}: {value}")
                else:
                    print("(no metadata)")

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
    except _SkipExisting as exc:
        print(f"Skipped: {exc}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1
