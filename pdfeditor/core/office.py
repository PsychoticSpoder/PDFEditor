"""Microsoft Word/Excel to PDF conversion via COM (Windows only)."""

from __future__ import annotations

import importlib
import logging
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .constants import (
    DEFAULT_EXCEL_PAPER_SIZE,
    EXCEL_PAPER_SIZES,
    OFFICE_BATCH_RESTART_EVERY,
)
from .io_utils import (
    OfficeProgressCallback,
    _collect_input_files,
    _emit_office_progress,
    _ensure_pdf_output,
    _filter_office_batch_files,
)
from .pdf_ops import _combine_pdfs_in_order

logger = logging.getLogger(__name__)


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


@contextmanager
def _com_application(prog_id: str):
    """Start a COM application and guarantee it is quit and COM is uninitialized.

    Replaces the copy-pasted COM setup/teardown that previously lived inside every
    Word/Excel function.
    """
    win32 = _get_windows_com_client()
    pythoncom = _initialize_com_if_available()
    app = None
    try:
        app = win32.DispatchEx(prog_id)
        app.Visible = False
        try:
            app.DisplayAlerts = False
        except Exception:  # noqa: BLE001 - Word expects 0/1 instead of a bool
            try:
                app.DisplayAlerts = 0
            except Exception:  # noqa: BLE001
                pass
        yield app
    finally:
        try:
            if app is not None:
                app.Quit()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to quit COM application %s: %s", prog_id, exc)
        if pythoncom is not None:
            try:
                pythoncom.CoUninitialize()
            except Exception:  # noqa: BLE001
                pass


def _configure_excel_worksheet(worksheet, paper_size: str) -> None:
    """Apply print settings so a worksheet exports cleanly to PDF.

    Fits all columns on one page wide (natural height), clears manual page breaks,
    picks orientation from the column count, and applies standard margins.
    """
    try:
        worksheet.ResetAllPageBreaks()
    except Exception:  # noqa: BLE001
        pass

    page_setup = worksheet.PageSetup
    try:
        page_setup.PrintArea = ""
        page_setup.FitToPagesWide = 1
        page_setup.FitToPagesTall = False
        page_setup.Zoom = False
        column_count = worksheet.UsedRange.Columns.Count
        page_setup.Orientation = 1 if column_count > 10 else 2  # 1=Landscape, 2=Portrait
    except Exception:  # noqa: BLE001
        page_setup.FitToPagesWide = 1
        page_setup.FitToPagesTall = False
        page_setup.Zoom = False

    page_setup.PaperSize = EXCEL_PAPER_SIZES.get(paper_size, EXCEL_PAPER_SIZES["A4"])
    page_setup.CenterHorizontally = True
    page_setup.CenterVertically = False
    page_setup.LeftMargin = 0.5 * 72
    page_setup.RightMargin = 0.5 * 72
    page_setup.TopMargin = 0.75 * 72
    page_setup.BottomMargin = 0.75 * 72
    page_setup.HeaderMargin = 0.3 * 72
    page_setup.FooterMargin = 0.3 * 72


def word_to_pdf(input_file: Path, output_file: Path) -> None:
    """Convert a Word document to PDF."""
    with _com_application("Word.Application") as word_app:
        document = None
        try:
            document = word_app.Documents.Open(str(input_file))
            if hasattr(document, "SaveAs2"):
                document.SaveAs2(str(output_file), FileFormat=17)
            else:
                document.SaveAs(str(output_file), FileFormat=17)
        finally:
            try:
                if document is not None:
                    document.Close(False)
            except Exception:  # noqa: BLE001
                pass
    _ensure_pdf_output(output_file)


def excel_to_pdf(
    input_file: Path,
    output_file: Path,
    paper_size: str = DEFAULT_EXCEL_PAPER_SIZE,
) -> None:
    """Convert an Excel workbook to PDF with all columns fitted on one page.

    Args:
        input_file: Path to the input Excel file.
        output_file: Path to the output PDF file.
        paper_size: Paper size for PDF export (Letter, Legal, A4, A3, Tabloid, Ledger).

    Raises:
        RuntimeError: If conversion fails or output file is not created.
    """
    with _com_application("Excel.Application") as excel_app:
        workbook = None
        try:
            workbook = excel_app.Workbooks.Open(str(input_file))
            for worksheet in workbook.Worksheets:
                _configure_excel_worksheet(worksheet, paper_size)
            workbook.ExportAsFixedFormat(0, str(output_file))
        finally:
            try:
                if workbook is not None:
                    workbook.Close(False)
            except Exception:  # noqa: BLE001
                pass
    _ensure_pdf_output(output_file)


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
    _get_windows_com_client()  # fail fast if pywin32/Windows are unavailable
    error_list = errors if errors is not None else []
    total = len(files)
    written_files: list[Path] = []

    for start in range(0, total, OFFICE_BATCH_RESTART_EVERY):
        chunk = files[start : start + OFFICE_BATCH_RESTART_EVERY]
        with _com_application("Word.Application") as word_app:
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
                except Exception as exc:  # noqa: BLE001
                    message = f"{input_file.name}: {exc}"
                    error_list.append(message)
                    _emit_office_progress(
                        progress_callback, index, total, input_file, f"error: {exc}"
                    )
                    if not continue_on_error:
                        raise RuntimeError(message) from exc
                finally:
                    try:
                        if document is not None:
                            document.Close(False)
                    except Exception:  # noqa: BLE001
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

    Each worksheet is exported into a temporary directory (never the user's output
    folder) and the resulting single-sheet PDFs are combined into one file per
    workbook. OSError/COM errors on a single file are collected rather than aborting
    the whole batch when ``continue_on_error`` is set.
    """
    files = _filter_office_batch_files(_collect_input_files(input_dir, pattern))
    _get_windows_com_client()  # fail fast if pywin32/Windows are unavailable
    error_list = errors if errors is not None else []
    total = len(files)
    written_files: list[Path] = []

    for start in range(0, total, OFFICE_BATCH_RESTART_EVERY):
        chunk = files[start : start + OFFICE_BATCH_RESTART_EVERY]
        with _com_application("Excel.Application") as excel_app:
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_path = Path(tmp_dir)
                for offset, input_file in enumerate(chunk, start=1):
                    index = start + offset
                    output_file = output_dir / f"{input_file.stem}.pdf"
                    _emit_office_progress(progress_callback, index, total, input_file, "start")

                    workbook = None
                    temp_pdfs: list[Path] = []
                    try:
                        workbook = excel_app.Workbooks.Open(str(input_file), ReadOnly=True)
                        for sheet_index, worksheet in enumerate(workbook.Worksheets):
                            temp_pdf = tmp_path / f"sheet_{sheet_index:03d}.pdf"
                            try:
                                _configure_excel_worksheet(worksheet, paper_size)
                                worksheet.ExportAsFixedFormat(0, str(temp_pdf))
                                temp_pdfs.append(temp_pdf)
                            except Exception as ws_exc:  # noqa: BLE001
                                logger.warning(
                                    "Worksheet %s export failed: %s", worksheet.Name, ws_exc
                                )

                        if not temp_pdfs:
                            raise RuntimeError("No worksheets were exported")

                        if len(temp_pdfs) == 1:
                            shutil.copyfile(temp_pdfs[0], output_file)
                        else:
                            _combine_pdfs_in_order(
                                temp_pdfs, output_file, skip_blank_pages=True
                            )
                        _ensure_pdf_output(output_file)
                        written_files.append(output_file)
                        _emit_office_progress(progress_callback, index, total, input_file, "ok")
                    except Exception as exc:  # noqa: BLE001
                        message = f"{input_file.name}: {exc}"
                        error_list.append(message)
                        _emit_office_progress(
                            progress_callback, index, total, input_file, f"error: {exc}"
                        )
                        if not continue_on_error:
                            raise RuntimeError(message) from exc
                    finally:
                        try:
                            if workbook is not None:
                                workbook.Close(False)
                        except Exception:  # noqa: BLE001
                            pass

    return written_files
