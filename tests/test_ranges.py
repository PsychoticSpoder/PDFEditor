"""Tests for page range / page order parsing."""

from __future__ import annotations

import pytest

from pdfeditor.core.ranges import _parse_page_order, _parse_page_range


def test_parse_page_range_none_selects_all():
    assert _parse_page_range(None, 3) == {0, 1, 2}


def test_parse_page_range_single_mixed_and_ranges():
    assert _parse_page_range("1,3,5-7", 10) == {0, 2, 4, 5, 6}


def test_parse_page_range_out_of_bounds():
    with pytest.raises(ValueError):
        _parse_page_range("0", 3)
    with pytest.raises(ValueError):
        _parse_page_range("4", 3)


def test_parse_page_range_reversed_range():
    with pytest.raises(ValueError):
        _parse_page_range("5-1", 10)


def test_parse_page_range_empty_spec_raises():
    with pytest.raises(ValueError):
        _parse_page_range(",", 3)


def test_parse_page_order_valid():
    assert _parse_page_order("3,1,2", 3) == [2, 0, 1]


def test_parse_page_order_missing_pages():
    with pytest.raises(ValueError):
        _parse_page_order("1,2", 3)


def test_parse_page_order_duplicates():
    with pytest.raises(ValueError):
        _parse_page_order("1,1,2", 3)
