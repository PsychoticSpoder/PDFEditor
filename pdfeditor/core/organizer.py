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
from typing import Callable

from pypdf import PdfReader

from .pdf_ops import (
    delete_page_range,
    extract_pages,
    get_metadata,
    insert_pages,
    reorder_pages,
    rotate_pdf,
    set_metadata,
    split_pdf,
    watermark_pdf,
)

logger = logging.getLogger(__name__)

__all__ = ["PageOrganizerDocument"]


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
        """The working copy, including every edit applied so far."""
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
        """Number of pages in the current working copy."""
        return len(PdfReader(str(self._working)).pages)

    def suggest_output_name(self, suffix: str = "_organized") -> str:
        """A sensible default filename for the ``save_as``/``extract`` prompts."""
        return f"{self._source.stem}{suffix}.pdf"

    # -- page operations -------------------------------------------------------
    def reorder(self, order_spec: str) -> None:
        """Reorder every page, e.g. ``"3,1,2"``."""
        self._apply(lambda output: reorder_pages(self._working, output, order_spec))

    def rotate(self, pages: str | None, angle: int) -> None:
        """Rotate ``pages`` (every page when ``None``) by ``angle`` degrees."""
        self._apply(lambda output: rotate_pdf(self._working, output, angle, pages))

    def delete(self, pages: str) -> None:
        """Remove ``pages`` from the document."""
        self._apply(lambda output: delete_page_range(self._working, output, pages))

    def watermark(
        self,
        pages: str | None,
        text: str,
        opacity: float,
        font_size: int,
    ) -> None:
        """Stamp ``text`` across ``pages`` (every page when ``None``)."""
        self._apply(
            lambda output: watermark_pdf(self._working, output, text, opacity, font_size, pages)
        )

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
        self._apply(
            lambda output: insert_pages(self._working, insert_path, output, position, pages)
        )

    def get_metadata(self) -> dict[str, str]:
        """Document metadata, keyed without the leading slash (e.g. ``Title``)."""
        return get_metadata(self._working)

    def set_metadata(self, metadata: dict[str, str]) -> None:
        """Update the supplied metadata keys on the working copy."""
        self._apply(lambda output: set_metadata(self._working, output, metadata))

    # -- exports (these leave the working copy untouched) ----------------------
    def extract(self, pages: str, output_file: Path | str) -> Path:
        """Write ``pages`` to a separate PDF."""
        destination = Path(output_file).expanduser()
        extract_pages(self._working, destination, pages)
        return destination

    def split(
        self,
        output_dir: Path | str,
        pages: str | None = None,
        prefix: str | None = None,
    ) -> list[Path]:
        """Write one PDF per page into ``output_dir``."""
        return split_pdf(self._working, Path(output_dir).expanduser(), pages, prefix)

    def save_as(self, output_file: Path | str) -> Path:
        """Copy the working copy to ``output_file`` and remember where it went."""
        destination = Path(output_file).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._working, destination)
        self._saved_path = destination
        self._dirty = False
        logger.info(f"Saved organized PDF: {destination}")
        return destination

    def revert(self) -> None:
        """Discard every edit by copying the original PDF over the working copy."""
        shutil.copyfile(self._source, self._working)
        self._saved_path = None
        self._dirty = False

    # -- internals -------------------------------------------------------------
    def _apply(self, operation: Callable[[Path], None]) -> None:
        """Run ``operation`` into a staging file, then swap it into the working copy."""
        operation(self._staging)
        self._staging.replace(self._working)
        self._saved_path = None
        self._dirty = True
