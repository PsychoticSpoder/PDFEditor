"""Path validation, file discovery, and parallel-processing helpers."""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import tempfile
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")
OfficeProgressCallback = Callable[[int, int, Path, str], None]


class OperationCancelled(RuntimeError):
    """Raised when a user-requested cancellation stops a long-running operation."""


def _validate_existing_file(path_text: str) -> Path:
    path = Path(path_text).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    return path


def _validate_output_path(path_text: str) -> Path:
    path = Path(path_text).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _unique_path(path: Path) -> Path:
    """Return ``path`` when it is free, otherwise ``stem (n)`` with the first free ``n``.

    The GUI uses this to avoid clobbering an existing output: ``report_rotated.pdf`` becomes
    ``report_rotated (1).pdf`` when the original name is already taken.
    """
    if not path.exists():
        return path
    counter = 1
    while True:
        candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


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


def _parallel_process(
    items: list[Path],
    processor: Callable[[Path], T],
    workers: int | None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[T]:
    """Process items in parallel using a thread pool.

    Args:
        items: List of items to process.
        processor: Function to apply to each item.
        workers: Number of worker threads (None for auto-detection).
        should_cancel: Optional predicate; when True, processing stops and
            OperationCancelled is raised.

    Returns:
        List of processed results in the same order as input items.

    Raises:
        RuntimeError: If parallel processing fails to return all results.
    """
    if not items:
        return []

    max_workers = _resolve_workers(workers)
    if max_workers == 1 or len(items) == 1:
        results_serial: list[T] = []
        for item in items:
            if should_cancel is not None and should_cancel():
                raise OperationCancelled("Operation cancelled.")
            results_serial.append(processor(item))
        return results_serial

    results: list[T | None] = [None] * len(items)
    executor = ThreadPoolExecutor(max_workers=max_workers)
    try:
        future_to_index = {
            executor.submit(processor, item): index for index, item in enumerate(items)
        }
        for future in as_completed(future_to_index):
            if should_cancel is not None and should_cancel():
                for pending in future_to_index:
                    pending.cancel()
                raise OperationCancelled("Operation cancelled.")
            index = future_to_index[future]
            results[index] = future.result()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

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


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (temp file in the same folder + os.replace).

    Writing to a sibling temp file and replacing prevents a partially written or
    corrupt output file from being left behind if the operation fails midway.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(tmp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise


def _atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    _atomic_write_bytes(path, text.encode(encoding))


def _atomic_write_pdf(writer, path: Path) -> None:
    """Serialize a PdfWriter to ``path`` atomically."""
    buffer = io.BytesIO()
    writer.write(buffer)
    _atomic_write_bytes(path, buffer.getvalue())


class Deadline:
    """Invoke a callback if a task is not (re)armed within ``timeout`` seconds.

    Used as a watchdog to force-kill a hung Office process. ``timeout <= 0`` disables
    it. ``bump()`` re-arms the timer, so long-running batches can reset it after each
    successful step and still catch a single stuck conversion.
    """

    def __init__(self, timeout: float, on_timeout: Callable[[], None]) -> None:
        self._timeout = timeout
        self._on_timeout = on_timeout
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._fired = False

    def start(self) -> None:
        self.bump()

    def bump(self) -> None:
        with self._lock:
            self._cancel_locked()
            if self._timeout and self._timeout > 0:
                self._timer = threading.Timer(self._timeout, self._fire)
                self._timer.daemon = True
                self._timer.start()

    def cancel(self) -> None:
        with self._lock:
            self._cancel_locked()

    @property
    def fired(self) -> bool:
        return self._fired

    def _cancel_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _fire(self) -> None:
        self._fired = True
        try:
            self._on_timeout()
        except Exception:  # noqa: BLE001
            pass
