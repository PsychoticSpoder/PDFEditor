"""Backward-compatibility shim for the old ``pdf_gui`` module.

The implementation now lives in ``pdfeditor.gui``. Prefer the ``pdf-editor-gui``
console script or ``python -m pdfeditor.gui``.
"""

from __future__ import annotations

from pdfeditor.gui import MainWindow, main

if __name__ == "__main__":
    raise SystemExit(main())
