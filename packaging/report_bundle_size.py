"""Report what actually makes up the size of a PyInstaller one-file build.

A one-file ``PDFEditor.exe`` is around 50 MB even though ``pdfeditor`` itself is only a few
hundred KB of Python: the bulk is the bundled runtime and its libraries, above all Qt 6. This
script opens a built archive with PyInstaller's own reader and prints where the bytes go, so
"why is the download so large?" can be answered from the artifact instead of guessed at:

    .venv\\Scripts\\python.exe packaging\\report_bundle_size.py dist\\PDFEditor.exe

It needs PyInstaller importable, because the listing comes from ``PyInstaller.archive.readers``;
the environment that built the exe already has it. ``.github/workflows/release.yml`` runs it for
both exes and appends the output to the run's step summary, so each release records its own
breakdown.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator
from pathlib import Path

try:  # A build-time tool: a plain runtime install of the app does not carry PyInstaller.
    from PyInstaller.archive.readers import CArchiveReader
except ImportError:  # pragma: no cover - the build environment always has PyInstaller
    CArchiveReader = None

#: How many individual entries to list after the per-category table.
DEFAULT_TOP = 15

#: Width of the category column in the report.
_WIDTH = 46

_MIB = 1024 * 1024


def _basename(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1]


def _categorize(name: str, typecode: str) -> str:
    """Bucket one archive entry by what shipped it; the first matching rule wins."""
    lower = name.replace("\\", "/").lower()
    base = _basename(lower)

    if typecode == "z":
        return "Frozen Python bytecode (PYZ)"
    if base == "base_library.zip" or (base.startswith("python3") and base.endswith(".dll")):
        return "Python runtime (interpreter + stdlib)"
    if base.startswith("qt6") and base.endswith(".dll"):
        return "Qt 6 libraries (C++)"
    if lower.startswith("pyside6/plugins/"):
        return "Qt plugins (platforms, styles, image formats)"
    if lower.startswith("pyside6/") and base.endswith(".pyd"):
        return "PySide6 bindings (.pyd)"
    if lower.startswith("pyside6/"):
        return "PySide6 (other)"
    if lower.startswith("reportlab/") or base.startswith("reportlab"):
        return "reportlab (PDF drawing)"
    if lower.startswith("pil/") or base.startswith("_imaging"):
        return "Pillow (PIL)"
    if lower.startswith("pypdf/"):
        return "pypdf"
    if lower.startswith(("win32com/", "pythonwin/", "pywin32_system32/")) or base.startswith(
        ("pythoncom", "pywintypes", "win32")
    ):
        return "pywin32 (COM automation)"
    return "Other"


def _entries(archive: object) -> Iterator[tuple[str, int, int, str]]:
    """Yield ``(name, stored_bytes, unpacked_bytes, typecode)`` for a CArchive table of contents.

    PyInstaller 6.x stores ``toc`` as ``{name: (offset, length, unpacked, flag, typecode)}``;
    older releases used ``[(name, (offset, length, unpacked, flag, typecode))]``. Both end in the
    same five fields, so one slice reads either.
    """
    toc = archive.toc
    items = toc.items() if isinstance(toc, dict) else toc
    for name, entry in items:
        _offset, length, unpacked, _flag, typecode = entry[-5:]
        yield name, int(length), int(unpacked), typecode


def _mb(num_bytes: float) -> str:
    return f"{num_bytes / _MIB:,.1f} MB"


def _line(label: str, files: str, stored: str, unpacked: str, share: str) -> str:
    return f"  {label:<{_WIDTH}}{files:>6}{stored:>11}{unpacked:>11}{share:>8}"


def report(path: Path, top: int) -> None:
    """Print the size breakdown of one one-file build to stdout."""
    if CArchiveReader is None:
        raise RuntimeError("PyInstaller is not importable")

    on_disk = path.stat().st_size
    entries = list(_entries(CArchiveReader(str(path))))

    totals: dict[str, list[int]] = {}
    for name, stored, unpacked, typecode in entries:
        bucket = totals.setdefault(_categorize(name, typecode), [0, 0, 0])
        bucket[0] += 1
        bucket[1] += stored
        bucket[2] += unpacked

    bundled = sum(stored for _name, stored, _unpacked, _typecode in entries)
    rule = "  " + "-" * (_WIDTH + 6 + 11 + 11 + 8)

    print(f"{path.name} - {_mb(on_disk)} on disk ({on_disk:,} bytes)")
    print(f"{len(entries)} bundled entries hold {_mb(bundled)} of that file")
    print()
    print(_line("Category", "Files", "On disk", "Unpacked", "Share"))
    print(rule)
    for label, (count, stored, unpacked) in sorted(
        totals.items(), key=lambda item: item[1][1], reverse=True
    ):
        share = stored / on_disk * 100 if on_disk else 0.0
        print(_line(label, str(count), _mb(stored), _mb(unpacked), f"{share:.1f}%"))
    print(rule)
    overhead = on_disk - bundled
    share = overhead / on_disk * 100 if on_disk else 0.0
    print(_line("Bootloader + archive overhead", "", _mb(overhead), "", f"{share:.1f}%"))
    print()
    print(f"  Largest {top} entries:")
    for name, stored, _unpacked, _typecode in sorted(
        entries, key=lambda item: item[1], reverse=True
    )[:top]:
        print(f"    {_mb(stored):>10}  {name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("archives", nargs="+", metavar="ARCHIVE", help="built one-file exe(s)")
    parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP,
        help=f"how many of the largest entries to list (default: {DEFAULT_TOP})",
    )
    args = parser.parse_args(argv)

    if CArchiveReader is None:
        print(
            "PyInstaller is not importable - run this with the environment that built the exe.",
            file=sys.stderr,
        )
        return 2

    status = 0
    for index, item in enumerate(args.archives):
        path = Path(item)
        if index:
            print()
        if not path.is_file():
            print(f"{path}: no such file", file=sys.stderr)
            status = 1
            continue
        try:
            report(path, args.top)
        except Exception as exc:  # pragma: no cover - needs a real build to exercise
            print(f"{path}: not a readable PyInstaller archive ({exc})", file=sys.stderr)
            status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
