"""Tests for validation, file discovery and atomic-write helpers."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from pdfeditor.core.io_utils import (
    Deadline,
    OperationCancelled,
    _atomic_write_bytes,
    _atomic_write_text,
    _collect_input_files,
    _filter_office_batch_files,
    _is_office_temp_file,
    _parallel_process,
    _resolve_workers,
    _validate_existing_file,
    _validate_output_dir,
)


def test_validate_existing_file_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        _validate_existing_file(str(tmp_path / "nope.pdf"))


def test_validate_existing_file_ok(tmp_path):
    target = tmp_path / "a.pdf"
    target.write_bytes(b"%PDF-1.4")
    assert _validate_existing_file(str(target)) == target


def test_validate_output_dir_creates(tmp_path):
    target = tmp_path / "nested" / "out"
    assert _validate_output_dir(str(target)) == target
    assert target.is_dir()


def test_collect_input_files_no_match(tmp_path):
    with pytest.raises(ValueError):
        _collect_input_files(tmp_path, "*.pdf")


def test_collect_input_files_sorted(tmp_path):
    (tmp_path / "b.pdf").write_bytes(b"%PDF")
    (tmp_path / "a.pdf").write_bytes(b"%PDF")
    (tmp_path / "c.txt").write_text("x")
    files = _collect_input_files(tmp_path, "*.pdf")
    assert [path.name for path in files] == ["a.pdf", "b.pdf"]


def test_is_office_temp_file():
    assert _is_office_temp_file(Path("~$doc.docx"))
    assert _is_office_temp_file(Path("x.tmp"))
    assert not _is_office_temp_file(Path("doc.docx"))


def test_filter_office_batch_files_all_temp():
    with pytest.raises(ValueError):
        _filter_office_batch_files([Path("~$a.docx")])


def test_resolve_workers():
    assert _resolve_workers(1) == 1
    assert _resolve_workers(None) >= 1
    with pytest.raises(ValueError):
        _resolve_workers(-1)


def test_atomic_write_bytes(tmp_path):
    target = tmp_path / "out.bin"
    _atomic_write_bytes(target, b"hello")
    assert target.read_bytes() == b"hello"
    # No stray temp files left behind.
    assert [p.name for p in tmp_path.iterdir()] == ["out.bin"]


def test_atomic_write_text(tmp_path):
    target = tmp_path / "out.txt"
    _atomic_write_text(target, "line1\nline2")
    assert target.read_text(encoding="utf-8") == "line1\nline2"


def test_deadline_fires_on_timeout():
    fired: list[bool] = []
    deadline = Deadline(0.05, lambda: fired.append(True))
    deadline.start()
    time.sleep(0.2)
    assert deadline.fired is True
    assert fired == [True]


def test_deadline_disabled_when_non_positive():
    deadline = Deadline(0, lambda: None)
    deadline.start()
    time.sleep(0.05)
    assert deadline.fired is False


def test_deadline_cancel_prevents_fire():
    fired: list[bool] = []
    deadline = Deadline(0.05, lambda: fired.append(True))
    deadline.start()
    deadline.cancel()
    time.sleep(0.15)
    assert fired == []


def test_deadline_bump_resets_timer():
    fired: list[bool] = []
    deadline = Deadline(0.15, lambda: fired.append(True))
    deadline.start()
    time.sleep(0.08)
    deadline.bump()
    time.sleep(0.08)
    assert fired == []  # bumped before the first window elapsed
    time.sleep(0.15)
    assert fired == [True]


def test_parallel_process_processes_all_items():
    items = [Path("a"), Path("b"), Path("c")]
    assert _parallel_process(items, lambda item: item.name, 1) == ["a", "b", "c"]


def test_parallel_process_honours_cancel():
    items = [Path("a"), Path("b")]
    with pytest.raises(OperationCancelled):
        _parallel_process(items, lambda item: item, 1, should_cancel=lambda: True)
