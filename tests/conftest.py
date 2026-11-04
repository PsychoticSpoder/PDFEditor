"""Shared pytest fixtures: build tiny PDFs and images on the fly."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image
from reportlab.pdfgen import canvas

# Keep any Qt-based tests headless (no display required).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def make_pdf(tmp_path):
    """Return a factory that writes a small multi-page PDF."""

    def _make(name: str = "input.pdf", pages: int = 3) -> Path:
        path = tmp_path / name
        pdf_canvas = canvas.Canvas(str(path))
        for index in range(pages):
            pdf_canvas.setFont("Helvetica", 12)
            pdf_canvas.drawString(72, 720, f"Page {index + 1} content line for text extraction.")
            pdf_canvas.showPage()
        pdf_canvas.save()
        return path

    return _make


@pytest.fixture
def make_image(tmp_path):
    """Return a factory that writes a small solid-colour image."""

    def _make(name: str = "img.png", size: tuple[int, int] = (200, 100)) -> Path:
        path = tmp_path / name
        Image.new("RGB", size, (200, 30, 30)).save(path)
        return path

    return _make
