# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the portable Windows build (see README, "Portable Windows build").

Run it from the repository root:

    pyinstaller --noconfirm --clean packaging/PDFEditor.spec
    set PDFEDITOR_CONSOLE=1
    pyinstaller --noconfirm --clean packaging/PDFEditor.spec

The first command writes ``dist/PDFEditor.exe`` (windowed, the one users download); the second
writes ``dist/PDFEditor-console.exe``, the very same program with a console attached, so a user
hitting a problem can run it and send the output. Everything else about the two builds is
identical because it all lives here.

Notes for whoever changes this file:

* ``pdfeditor/core/office.py`` reaches pywin32 through ``importlib.import_module``, which
  PyInstaller cannot see, so ``win32com``/``pythoncom`` have to be listed as hidden imports.
* pywin32's extension DLLs live in ``site-packages/pywin32_system32``, outside any importable
  package, and are collected below by path.
* QtPdf is what draws the page thumbnails; without it the app still runs but reports
  "Thumbnails unavailable (QtPdf missing)", so the release workflow checks the built archive
  for it (see .github/workflows/release.yml).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

SPEC_DIR = Path(SPECPATH).resolve()
ROOT = SPEC_DIR.parent
sys.path.insert(0, str(ROOT))

from pdfeditor import __version__  # noqa: E402  (the exe's Properties show this version)

CONSOLE = bool(os.environ.get("PDFEDITOR_CONSOLE"))
NAME = "PDFEditor-console" if CONSOLE else "PDFEditor"

# Nothing below is imported by the app: the list only stops PyInstaller's hooks from dragging
# extra Qt modules (and their DLLs) into a bundle that has no use for them.
EXCLUDES = [
    "PySide6.Qt3DCore",
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    "matplotlib",
    "numpy",
    "pytest",
    "tkinter",
]

HIDDEN_IMPORTS = [
    "PySide6.QtPdf",  # thumbnails/page organizer; imported behind a try/except at run time
    "pythoncom",
    "win32com",
    "win32com.client",
    "win32timezone",
    "pywintypes",
]


def pywin32_system_dlls() -> list[tuple[str, str]]:
    """pywin32's extension DLLs, which do not sit inside an importable package.

    ``pywintypes313.dll`` and ``pythoncom313.dll`` are loaded by name from the bundle root at
    run time, which is where PyInstaller's own pywintypes hook puts them too.
    """
    for entry in sys.path:
        folder = Path(entry) / "pywin32_system32"
        if folder.is_dir():
            return sorted((str(dll), ".") for dll in folder.glob("*.dll"))
    return []


def version_resource() -> str:
    """Write the Windows version resource, so the exe's Properties tab is not blank."""
    parts = [int(part) for part in re.findall(r"\d+", __version__)][:4]
    parts += [0] * (4 - len(parts))
    numbers = ", ".join(str(part) for part in parts)
    build_dir = ROOT / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    target = build_dir / f"version_info-{NAME}.txt"
    target.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({numbers}),
    prodvers=({numbers}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'PDF Editor Tool'),
        StringStruct('FileDescription', 'PDF Editor Tool'),
        StringStruct('FileVersion', '{__version__}'),
        StringStruct('InternalName', '{NAME}'),
        StringStruct('OriginalFilename', '{NAME}.exe'),
        StringStruct('ProductName', 'PDF Editor Tool'),
        StringStruct('ProductVersion', '{__version__}'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""",
        encoding="utf-8",
    )
    return str(target)


print(f"Building {NAME}.exe from {ROOT} (pdfeditor {__version__}, console={CONSOLE})")

a = Analysis(
    [str(ROOT / "pdf_gui.py")],
    pathex=[str(ROOT)],
    binaries=pywin32_system_dlls(),
    datas=[],
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX squeezes the bundle a little but is known to break Qt DLLs and to trip antivirus
    # heuristics, so the download stays uncompressed.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=CONSOLE,
    # A windowed build has nowhere to print a traceback, so let PyInstaller show a dialog.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(SPEC_DIR / "PDFEditor.ico"),
    version=version_resource(),
)
