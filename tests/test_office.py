"""Tests for Office (Excel) helper logic that do not require a running Office app."""

from __future__ import annotations

from pdfeditor.core.office import _configure_excel_worksheet


class _FakeColumns:
    def __init__(self, count: int) -> None:
        self.Count = count


class _FakeUsedRange:
    def __init__(self, column_count: int) -> None:
        self.Columns = _FakeColumns(column_count)


class _FakePageSetup:
    pass


class _FakeWorksheet:
    def __init__(self, column_count: int) -> None:
        self.PageSetup = _FakePageSetup()
        self.UsedRange = _FakeUsedRange(column_count)
        self.reset_called = False

    def ResetAllPageBreaks(self) -> None:  # noqa: N802 - mimics the COM API
        self.reset_called = True


def test_configure_excel_worksheet_portrait_for_few_columns():
    worksheet = _FakeWorksheet(column_count=5)
    _configure_excel_worksheet(worksheet, "A4")

    page_setup = worksheet.PageSetup
    assert worksheet.reset_called is True
    assert page_setup.FitToPagesWide == 1
    assert page_setup.FitToPagesTall is False
    assert page_setup.Zoom is False
    assert page_setup.Orientation == 2  # Portrait
    assert page_setup.PaperSize == 9  # A4


def test_configure_excel_worksheet_landscape_for_many_columns():
    worksheet = _FakeWorksheet(column_count=20)
    _configure_excel_worksheet(worksheet, "Letter")

    page_setup = worksheet.PageSetup
    assert page_setup.Orientation == 1  # Landscape
    assert page_setup.PaperSize == 1  # Letter
