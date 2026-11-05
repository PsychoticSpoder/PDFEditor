"""A working-copy editing session behind the GUI page organizer.

The organizer lets pages be dragged around and rotated/deleted/watermarked/etc. without
choosing an output file for every step. To keep that simple and robust, every action is
applied to a *working copy* of the source PDF inside a temporary folder, reusing the
operations in :mod:`pdfeditor.core.pdf_ops`. :meth:`PageOrganizerDocument.save_as` then
copies the finished document wherever the user wants it, and
:meth:`PageOrganizerDocument.revert` throws the edits away.

The original file is never modified, and nothing here touches Qt, so the whole session
can be tested on its own.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter

from .io_utils import _atomic_write_pdf
from .pdf_ops import _watermark_overlay_page, extract_pages, get_metadata, split_pdf
from .ranges import _parse_page_order, _parse_page_range

logger = logging.getLogger(__name__)

__all__ = ["PageOrganizerDocument"]

#: A recorded edit: (kind, args).
_Edit = tuple[str, tuple[Any, ...]]
#: A resolved page: (source file, page index in it, extra rotation, watermark stamp).
_Entry = tuple[Path, int, int, tuple[str, float, int] | None]


class PageOrganizerDocument:
    """An editable temporary copy of a PDF, plus the page operations run against it.

    Args:
        source: The PDF to organize. Editing never touches this file.
        working_dir: Folder to hold the working copy. A fresh temporary directory is
            created (and owned by this object) when it is omitted.
    """

    def __init__(self, source: Path | str, working_dir: Path | None = None) -> None:
        self._source = Path(source).expanduser().resolve()
        if not self._source.is_file():
            raise FileNotFoundError(f"PDF not found: {self._source}")
        self._owns_working_dir = working_dir is None
        if working_dir is None:
            self._working_dir = Path(tempfile.mkdtemp(prefix="pdf-organizer-"))
        else:
            self._working_dir = Path(working_dir)
            self._working_dir.mkdir(parents=True, exist_ok=True)
        self._working = self._working_dir / self._source.name
        self._staging = self._working_dir / f"_staging{self._source.suffix or '.pdf'}"
        self._saved_path: Path | None = None
        self._dirty = False
        shutil.copyfile(self._source, self._working)
        self._source_count = len(PdfReader(str(self._source)).pages)
        # Edits are recorded rather than written straight away, because rewriting the whole
        # document on every click is what made large PDFs feel slow. `_materialize` applies
        # them all in one pass, the first time a real file is actually needed.
        self._pending: list[_Edit] = []
        self._count = self._source_count
        self._materialized_count = self._source_count

    # -- lifecycle -------------------------------------------------------------
    def __enter__(self) -> PageOrganizerDocument:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - best-effort cleanup
        self.close()

    def close(self) -> None:
        """Delete the temporary working folder (only when this object created it)."""
        if not getattr(self, "_owns_working_dir", False):
            return
        self._owns_working_dir = False
        shutil.rmtree(self._working_dir, ignore_errors=True)

    # -- introspection ---------------------------------------------------------
    @property
    def source(self) -> Path:
        """The original PDF this session was opened from."""
        return self._source

    @property
    def path(self) -> Path:
        """The working copy, including every edit applied so far.

        Reading this writes any still-pending edits to disk first, so the file always
        matches what the grid shows. ``page_count`` and the metadata do not need that.
        """
        self._materialize()
        return self._working

    @property
    def saved_path(self) -> Path | None:
        """Where :meth:`save_as` last wrote the document, if it has been saved."""
        return self._saved_path

    @property
    def is_dirty(self) -> bool:
        """True when the working copy has edits that have not been saved yet."""
        return self._dirty

    def page_count(self) -> int:
        """Number of pages as edited so far (no file access, so it stays instant)."""
        return self._count

    def suggest_output_name(self, suffix: str = "_organized") -> str:
        """A sensible default filename for the ``save_as``/``extract`` prompts."""
        return f"{self._source.stem}{suffix}.pdf"

    # -- page operations -------------------------------------------------------
    # Each one records the edit (and updates the in-memory page count); the file itself is
    # written once, on demand.
    def reorder(self, order_spec: str) -> None:
        """Reorder every page, e.g. ``"3,1,2"``."""
        self._record("reorder", order_spec)

    def rotate(self, pages: str | None, angle: int) -> None:
        """Rotate ``pages`` (every page when ``None``) by ``angle`` degrees."""
        self._record("rotate", pages, angle)

    def delete(self, pages: str) -> None:
        """Remove ``pages`` from the document."""
        self._record("delete", pages)

    def watermark(
        self,
        pages: str | None,
        text: str,
        opacity: float,
        font_size: int,
    ) -> None:
        """Stamp ``text`` across ``pages`` (every page when ``None``)."""
        self._record("watermark", pages, text, opacity, font_size)

    def insert(
        self,
        insert_file: Path | str,
        position: int,
        pages: str | None = None,
    ) -> None:
        """Insert ``pages`` of ``insert_file`` before the 1-based ``position``."""
        insert_path = Path(insert_file).expanduser()
        if not insert_path.is_file():
            raise FileNotFoundError(f"PDF to insert not found: {insert_path}")
        self._record("insert", insert_path, position, pages)

    def get_metadata(self) -> dict[str, str]:
        """Document metadata, keyed without the leading slash (e.g. ``Title``)."""
        metadata = dict(get_metadata(self._working))
        for kind, args in self._pending:
            if kind == "metadata":
                metadata.update({str(key).lstrip("/"): value for key, value in args[0].items()})
        return metadata

    def set_metadata(self, metadata: dict[str, str]) -> None:
        """Update the supplied metadata keys on the working copy."""
        self._record("metadata", dict(metadata))

    # -- exports (these leave the working copy untouched) ----------------------
    def extract(self, pages: str, output_file: Path | str) -> Path:
        """Write ``pages`` to a separate PDF."""
        destination = Path(output_file).expanduser()
        extract_pages(self.path, destination, pages)
        return destination

    def split(
        self,
        output_dir: Path | str,
        pages: str | None = None,
        prefix: str | None = None,
        chunk_size: int | None = None,
    ) -> list[Path]:
        """Write one PDF per page — or per ``chunk_size`` pages — into ``output_dir``."""
        return split_pdf(
            self.path, Path(output_dir).expanduser(), pages, prefix, chunk_size
        )

    def save_as(self, output_file: Path | str) -> Path:
        """Write every pending edit, then copy the result to ``output_file``."""
        destination = Path(output_file).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.path, destination)
        self._saved_path = destination
        self._dirty = False
        logger.info(f"Saved organized PDF: {destination}")
        return destination

    def revert(self) -> None:
        """Discard every edit by copying the original PDF over the working copy."""
        self._pending.clear()
        shutil.copyfile(self._source, self._working)
        self._count = self._materialized_count = self._source_count
        self._saved_path = None
        self._dirty = False

    # -- internals -------------------------------------------------------------
    def _record(self, kind: str, *args: Any) -> None:
        """Note an edit and track its effect on the page list, without touching the file."""
        if kind == "delete":
            doomed = _parse_page_range(str(args[0]), self._count)
            if len(doomed) >= self._count:
                raise ValueError("Delete range removed all pages. Output PDF would be empty.")
            self._count -= len(doomed)
        elif kind in ("rotate", "watermark"):
            _parse_page_range(args[0], self._count)  # validate the spec up front
        elif kind == "reorder":
            _parse_page_order(str(args[0]), self._count)
        elif kind == "insert":
            insert_file, position, pages = args
            if not 1 <= int(position) <= self._count + 1:
                raise ValueError(f"Insert position must be between 1 and {self._count + 1}.")
            selected = _parse_page_range(pages, len(PdfReader(str(insert_file)).pages))
            self._count += len(selected)
        self._pending.append((kind, args))
        self._saved_path = None
        self._dirty = True

    def _materialize(self) -> None:
        """Write every pending edit to the working copy in a single pass."""
        pending, self._pending = self._pending, []
        if not pending:
            return
        entries: list[_Entry] = [
            (self._working, index, 0, None) for index in range(self._materialized_count)
        ]
        metadata: dict[str, str] = {}
        for kind, args in pending:
            if kind == "reorder":
                order = _parse_page_order(str(args[0]), len(entries))
                entries = [entries[index] for index in order]
            elif kind == "rotate":
                pages, angle = args[0], int(args[1])
                for index in _parse_page_range(pages, len(entries)):
                    path, page_index, rotation, stamp = entries[index]
                    entries[index] = (path, page_index, (rotation + angle) % 360, stamp)
            elif kind == "delete":
                doomed = _parse_page_range(str(args[0]), len(entries))
                entries = [entry for index, entry in enumerate(entries) if index not in doomed]
            elif kind == "insert":
                insert_file, position, pages = args
                reader = PdfReader(str(insert_file))
                selected = sorted(_parse_page_range(pages, len(reader.pages)))
                split_at = min(max(int(position) - 1, 0), len(entries))
                insert_path = Path(str(insert_file))
                entries[split_at:split_at] = [(insert_path, index, 0, None) for index in selected]
            elif kind == "watermark":
                pages, text, opacity, font_size = args
                stamp: tuple[str, float, int] = (str(text), float(opacity), int(font_size))
                for index in _parse_page_range(pages, len(entries)):
                    path, page_index, rotation, _ = entries[index]
                    entries[index] = (path, page_index, rotation, stamp)
            elif kind == "metadata":
                metadata.update(args[0])
        self._write(entries, metadata)
        self._materialized_count = self._count

    def _write(self, entries: list[_Entry], metadata: dict[str, str]) -> None:
        """Build the output document from the resolved page list in one pass."""
        readers: dict[Path, PdfReader] = {}
        writer = PdfWriter()
        for path, page_index, rotation, stamp in entries:
            reader = readers.get(path)
            if reader is None:
                reader = readers[path] = PdfReader(str(path))
            page = reader.pages[page_index]
            if rotation % 360:
                page.rotate(rotation)
            writer.add_page(page)
            if stamp is not None:
                text, opacity, font_size = stamp
                writer.pages[-1].merge_page(
                    _watermark_overlay_page(
                        float(page.mediabox.width),
                        float(page.mediabox.height),
                        text,
                        opacity,
                        font_size,
                    )
                )
        if metadata:
            writer.add_metadata({f"/{key.lstrip('/')}": value for key, value in metadata.items()})
        _atomic_write_pdf(writer, self._staging)
        self._staging.replace(self._working)
