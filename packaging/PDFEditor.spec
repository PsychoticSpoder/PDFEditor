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

# The GUI needs QtCore, QtGui and QtWidgets, plus QtNetwork (Qt links it) and QtPdf for the page
# thumbnails — QtPdf is a hidden import below and must never appear in this list.
#
# Everything else PySide6 ships is unreachable from a Widgets-only app, and Qt's core modules do
# not depend on it, so excluding a module here only drops DLLs nothing can load. PyInstaller's
# PySide6 hooks otherwise drag some of them in on their own, which is why the list has to name
# them: the rest of the "Addons" wheel (Qt3D, Charts, DataVisualization, Graphs, Location,
# Multimedia, NetworkAuth, Nfc, Positioning, Quick3D, RemoteObjects, Scxml, Sensors, SerialBus,
# SerialPort, SpatialAudio, StateMachine, TextToSpeech, VirtualKeyboard, WebChannel, WebSockets,
# WebView, HttpServer, AxContainer, CanvasPainter), the whole QML/Quick stack, Qt WebEngine (a
# separate wheel), and the unused "essentials" (Designer, Help, Sql, SvgWidgets, Test, UiTools,
# Xml). QtSvg, QtOpenGL and QtPrintSupport are deliberately kept: they are small, and Qt can
# reach for them while loading images, rendering or printing.
EXCLUDES = [
    # Qt "Addons" modules the app cannot reach. QtPdfWidgets is the widget view; the app uses
    # QPdfDocument from QtPdf directly, so only that one is kept.
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtAsyncio",
    "PySide6.QtAxContainer",
    "PySide6.QtBluetooth",
    "PySide6.QtCanvasPainter",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.QtGraphsWidgets",
    "PySide6.QtHttpServer",
    "PySide6.QtLocation",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNetworkAuth",
    "PySide6.QtNfc",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtQuick3D",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialBus",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtStateMachine",
    "PySide6.QtTextToSpeech",
    "PySide6.QtVirtualKeyboard",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtWebView",
    # Qt WebEngine is a separate wheel, and the app has no browser.
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    # The QML/Quick stack: the GUI is built from widgets, so none of this is imported.
    "PySide6.QtQml",
    "PySide6.QtQmlFeatures",
    "PySide6.QtQuick",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickTest",
    "PySide6.QtQuickWidgets",
    # The remaining unused "essentials".
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtSql",
    "PySide6.QtSvgWidgets",
    "PySide6.QtTest",
    "PySide6.QtUiTools",
    "PySide6.QtXml",
    # Nothing here is imported either.
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


# EXCLUDES removes *Python modules*. The Qt, Pillow and pywin32 hooks also copy plain files that no
# code path in the app can reach, and dropping those is what actually shrinks the download:
#   * opengl32sw.dll — Qt's software OpenGL fallback (~7 MB). A widgets app paints with the raster
#     engine, or through the platform plugin, so OpenGL is never requested and the file is never
#     loaded.
#   * PIL/_avif*.pyd — Pillow's AVIF codec (~4 MB). Images to PDF goes through Pillow, but AVIF is
#     not a format the tool offers; the codecs for PNG, JPEG, GIF and WebP are kept.
#   * PySide6/translations/*.qm — Qt's own UI translations. The app installs no QTranslator and its
#     dialogs are the native Windows ones, so no .qm file is ever opened.
#   * pythonwin/win32ui.pyd and PIL/_imagingtk*.pyd — pywin32's MFC layer and Pillow's Tk bridge;
#     office.py only uses win32com.client, and tkinter is excluded above.
#: Collected file *names* (not paths) that the app can never load.
TRIMMED_FILES = ("opengl32sw.dll", "win32ui.pyd")


def _is_trimmed(dest: str) -> bool:
    """True for a collected file the app can never load (see TRIMMED_FILES above)."""
    name = dest.replace("\\", "/").lower()
    base = name.rsplit("/", 1)[-1]
    if base in TRIMMED_FILES:
        return True
    if name.startswith("pyside6/translations/"):
        return True
    return base.startswith(("_avif.", "_imagingtk."))


def trim(entries):
    """Drop the files above, keeping ``entries``' own TOC type for the EXE call."""
    return type(entries)([entry for entry in entries if not _is_trimmed(entry[0])])


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

# The hooks collect a handful of files the app can never load (see _is_trimmed); drop them here,
# before the archive is assembled.
a.binaries = trim(a.binaries)
a.datas = trim(a.datas)

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
