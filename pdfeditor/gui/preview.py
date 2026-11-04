"""PDF preview and a thumbnail page-picker built on PySide6's bundled QtPdf.

QtPdf ships with PySide6, so no extra dependency is required. If it is unavailable in
a given build, the widgets degrade to a short message and the page-picker button is
hidden rather than failing.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

try:  # pragma: no cover - depends on the PySide6 build
    from PySide6.QtPdf import QPdfDocument

    _QT_PDF_AVAILABLE = True
except ImportError:  # pragma: no cover
    QPdfDocument = None  # type: ignore[assignment,misc]
    _QT_PDF_AVAILABLE = False


def qtpdf_available() -> bool:
    """True when the bundled QtPdf module can be imported."""
    return _QT_PDF_AVAILABLE


def _parse_spec_to_pages(spec: str) -> set[int]:
    """Expand a 1-based page spec like ``"1,3,5-7"`` into a set of page numbers."""
    pages: set[int] = set()
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start_text, _, end_text = token.partition("-")
            if start_text.strip().isdigit() and end_text.strip().isdigit():
                start, end = int(start_text), int(end_text)
                if start <= end:
                    pages.update(range(start, end + 1))
        elif token.isdigit():
            pages.add(int(token))
    return pages


def _format_page_selection(pages: list[int]) -> str:
    """Compress a list of 1-based page numbers into ``"1,3,5-7"`` form."""
    if not pages:
        return ""
    ordered = sorted(set(pages))
    parts: list[str] = []
    start = previous = ordered[0]
    for value in ordered[1:]:
        if value == previous + 1:
            previous = value
            continue
        parts.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = value
    parts.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(parts)


class PdfPreview(QWidget):
    """A small first-page preview with a page count."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.image = QLabel("No document")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setFixedSize(160, 200)
        self.image.setStyleSheet("border: 1px solid #dadce0; background: #f8f9fa;")
        self.info = QLabel("")
        self.info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.info.setStyleSheet("color: #5f6368;")
        layout.addWidget(self.image)
        layout.addWidget(self.info)

    def set_document(self, path: str | None) -> None:
        if not _QT_PDF_AVAILABLE or not path:
            self.image.setPixmap(QPixmap())
            self.image.setText("Preview unavailable")
            self.info.setText("")
            return
        document = QPdfDocument()
        document.load(path)
        page_count = document.pageCount()
        if page_count <= 0:
            self.image.setPixmap(QPixmap())
            self.image.setText("Could not load PDF")
            self.info.setText("")
            return
        image = document.render(0, QSize(160, 200))
        if image.isNull():
            self.image.setPixmap(QPixmap())
            self.image.setText("(no preview)")
        else:
            self.image.setPixmap(
                QPixmap.fromImage(image).scaled(
                    160,
                    200,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        self.info.setText(f"{page_count} page(s)")


class PagePickerDialog(QDialog):
    """A thumbnail grid for choosing pages, returning a range string."""

    def __init__(
        self,
        path: str,
        preselected: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select pages")
        self.setMinimumSize(560, 480)
        self._boxes: list[QCheckBox] = []

        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        grid = QGridLayout(content)
        scroll.setWidget(content)
        layout.addWidget(scroll)

        preselected_pages = _parse_spec_to_pages(preselected or "")
        document = QPdfDocument() if _QT_PDF_AVAILABLE else None
        page_count = 0
        if document is not None:
            document.load(path)
            page_count = document.pageCount()

        if page_count == 0:
            layout.addWidget(QLabel("Preview unavailable; enter the page range manually."))
        else:
            columns = 4
            for index in range(page_count):
                cell = QWidget()
                cell_layout = QVBoxLayout(cell)
                cell_layout.setContentsMargins(4, 4, 4, 4)
                thumbnail = QLabel()
                thumbnail.setFixedSize(110, 140)
                thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
                thumbnail.setStyleSheet("border: 1px solid #dadce0;")
                image = document.render(index, QSize(110, 140))
                if not image.isNull():
                    thumbnail.setPixmap(
                        QPixmap.fromImage(image).scaled(
                            110,
                            140,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
                box = QCheckBox(str(index + 1))
                box.setChecked((index + 1) in preselected_pages)
                self._boxes.append(box)
                cell_layout.addWidget(thumbnail)
                cell_layout.addWidget(box)
                grid.addWidget(cell, index // columns, index % columns)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_spec(self) -> str:
        return _format_page_selection(
            [index + 1 for index, box in enumerate(self._boxes) if box.isChecked()]
        )
