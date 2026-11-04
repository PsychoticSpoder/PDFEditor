"""Allow running the CLI via ``python -m pdfeditor``."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
