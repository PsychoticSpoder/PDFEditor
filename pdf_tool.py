"""Backward-compatibility shim for the old ``pdf_tool`` module.

The implementation now lives in the ``pdfeditor`` package. Prefer the ``pdf-tool``
console script, ``python -m pdfeditor``, or importing ``pdfeditor.core`` / ``pdfeditor.cli``
directly.
"""

from __future__ import annotations

from pdfeditor.cli import build_parser, main
from pdfeditor.core import *  # noqa: F401,F403  (re-export the public core API)

if __name__ == "__main__":
    raise SystemExit(main())
