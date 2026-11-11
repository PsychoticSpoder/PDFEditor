"""Draw ``packaging/PDFEditor.ico``, the icon the portable Windows build ships with.

The icon is generated rather than hand-drawn so it can be re-made (or tweaked) without a
binary asset editor:

    .venv\\Scripts\\python.exe packaging\\make_icon.py

It draws a page with a folded corner and a red "PDF" band at 4x the largest size and
down-samples with LANCZOS, which keeps the 16 px and 32 px versions (taskbar, Explorer
details view) legible.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

#: Sizes Windows picks from an .ico: Explorer/taskbar/alt-tab and the 256 px extra-large view.
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

SUPERSAMPLE = 4
BASE = 256 * SUPERSAMPLE
PAGE = (0xF7, 0xF8, 0xFA)
PAGE_EDGE = (0x5F, 0x63, 0x68)
FOLD = (0xDA, 0xDC, 0xE0)
BAND = (0xD9, 0x30, 0x25)
INK = (0xFF, 0xFF, 0xFF)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """A bold face for the band, falling back to Pillow's bundled scalable default."""
    for name in ("arialbd.ttf", "segoeuib.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def draw() -> Image.Image:
    """The icon at the largest size, on a transparent background."""
    image = Image.new("RGBA", (BASE, BASE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def box(ratio: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
        left, top, right, bottom = ratio
        return (
            round(left * BASE),
            round(top * BASE),
            round(right * BASE),
            round(bottom * BASE),
        )

    left, top, right, bottom = box((0.14, 0.10, 0.86, 0.90))
    fold = round(0.22 * BASE)  # the folded corner's size
    radius = round(0.035 * BASE)
    edge = round(0.012 * BASE)

    # The page, with its top-right corner folded over.
    draw.rounded_rectangle(
        (left, top, right, bottom), radius=radius, fill=PAGE, outline=PAGE_EDGE, width=edge
    )
    draw.polygon(
        [(right - fold, top), (right, top + fold), (right - fold, top + fold)],
        fill=FOLD,
    )
    draw.line(
        [(right - fold, top), (right - fold, top + fold), (right, top + fold)],
        fill=PAGE_EDGE,
        width=edge,
    )

    # The "PDF" band, which is what still reads as a document at 16 px.
    band = box((0.22, 0.56, 0.78, 0.76))
    draw.rounded_rectangle(band, radius=round(0.02 * BASE), fill=BAND)
    font = _font(round(0.13 * BASE))
    text = "PDF"
    text_left, text_top, text_right, text_bottom = draw.textbbox((0, 0), text, font=font)
    draw.text(
        (
            (band[0] + band[2] - (text_right - text_left)) / 2 - text_left,
            (band[1] + band[3] - (text_bottom - text_top)) / 2 - text_top,
        ),
        text,
        font=font,
        fill=INK,
    )
    return image


def main() -> None:
    largest = draw().resize((256, 256), Image.LANCZOS)
    target = Path(__file__).with_name("PDFEditor.ico")
    largest.save(target, format="ICO", sizes=SIZES)
    written = ", ".join(f"{width}x{height}" for width, height in SIZES)
    print(f"Wrote {target} ({target.stat().st_size} bytes, sizes: {written})")


if __name__ == "__main__":
    main()
